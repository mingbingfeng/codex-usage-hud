"""Renderer session-view JavaScript domain."""

TEXT = r"""
  function createSessionViewDomain(ctx, shared) {
  // ABI marker retained for existing renderer contract probes:
  // data-field="topTaskOrdinalSession"
  function lineInner(node) {
    let inner = node.querySelector(":scope > .codex-usage-hud-line-inner");
    if (!inner) {
      const text = node.textContent || "";
      node.textContent = "";
      inner = document.createElement("span");
      inner.className = "codex-usage-hud-line-inner";
      inner.textContent = text;
      node.appendChild(inner);
    }
    return inner;
  }


  function refreshMarquee(node) {
    if (!node?.classList?.contains("codex-usage-hud-line") || !node.isConnected) return;
    const inner = node.querySelector(":scope > .codex-usage-hud-line-inner");
    if (!inner) return;
    const available = Math.max(1, node.clientWidth || node.getBoundingClientRect().width || 0);
    const overflow = Math.ceil((inner.scrollWidth || inner.getBoundingClientRect().width || 0) - available);
    if (overflow > 1) {
      node.dataset.marquee = "true";
      node.style.setProperty("--codex-usage-hud-marquee-distance", `${overflow}px`);
      node.style.setProperty("--codex-usage-hud-marquee-duration", `${Math.max(4000, 3000 + (overflow * 60))}ms`);
      return;
    }
    delete node.dataset.marquee;
    node.style.removeProperty("--codex-usage-hud-marquee-distance");
    node.style.removeProperty("--codex-usage-hud-marquee-duration");
  }

  function refreshAllMarquees(root = document.getElementById(rootId)) {
    if (!root) return;
    root.querySelectorAll(".codex-usage-hud-progress-rail").forEach(budgetDomain.refreshProgressRailLabel);
    root.querySelectorAll(".codex-usage-hud-line").forEach(refreshMarquee);
    root.querySelectorAll(".codex-usage-hud-progress-strip").forEach(budgetDomain.refreshCollapsedProgressStrip);
    // 强制立即刷新一次，避免 HUD 打开很久才生效
    ctx.lifecycle.timeout(
      "session_view_progress",
      () => root.querySelectorAll(".codex-usage-hud-progress-rail").forEach(
        budgetDomain.refreshProgressRailLabel,
      ),
      0,
    );
  }

  function applyLineText(node, value, { refresh = true } = {}) {
    const text = String(value || "");
    const inner = lineInner(node);
    inner.textContent = text;
    node.dataset.currentText = text;
    if (refresh) ctx.lifecycle.frame("session_view", () => refreshMarquee(node));
  }

  function cancelNumericAnimation(node) {
    const animation = numericAnimations.get(node);
    if (!animation) return;
    ctx.lifecycle.clearFrame(animation.raf);
    numericAnimations.delete(node);
  }

  function extractNumericParts(text) {
    const source = String(text || "");
    const parts = [];
    const tokens = [];
    let cursor = 0;
    numericTokenRe.lastIndex = 0;
    for (const match of source.matchAll(numericTokenRe)) {
      parts.push(source.slice(cursor, match.index));
      tokens.push(match[0]);
      cursor = Number(match.index || 0) + match[0].length;
    }
    parts.push(source.slice(cursor));
    return { parts, tokens };
  }

  function parseNumericToken(token) {
    const match = String(token || "").match(/^(\$?)(\d+(?:,\d{3})*(?:\.\d+)?)([kM%]?)$/);
    if (!match) return null;
    const [, prefix, amount, suffix] = match;
    const decimals = amount.includes(".") ? amount.split(".", 2)[1].length : 0;
    const usesGrouping = amount.includes(",");
    const value = Number(amount.replace(/,/g, ""));
    if (!Number.isFinite(value)) return null;
    return { prefix, value, suffix, decimals, usesGrouping };
  }

  function formatNumericToken(value, template) {
    const decimals = Math.max(0, template.decimals || 0);
    let body = "";
    if (decimals <= 0) {
      const rounded = Math.round(value);
      body = template.usesGrouping ? rounded.toLocaleString("en-US") : String(rounded);
    } else if (template.usesGrouping) {
      body = value.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
    } else {
      body = value.toFixed(decimals);
    }
    return `${template.prefix}${body}${template.suffix}`;
  }

  function canAnimateNumericText(startText, endText) {
    const start = extractNumericParts(startText);
    const end = extractNumericParts(endText);
    if (!start.tokens.length || start.tokens.length !== end.tokens.length) return false;
    if (start.parts.length !== end.parts.length || start.parts.some((part, index) => part !== end.parts[index])) return false;
    return start.tokens.every((token, index) => {
      const startToken = parseNumericToken(token);
      const endToken = parseNumericToken(end.tokens[index]);
      return !!startToken && !!endToken && startToken.prefix === endToken.prefix && startToken.suffix === endToken.suffix;
    });
  }

  function interpolateNumericText(startText, endText, progress) {
    const start = extractNumericParts(startText);
    const end = extractNumericParts(endText);
    const clamped = clamp(progress, 0, 1);
    const pieces = [];
    for (let index = 0; index < end.parts.length - 1; index += 1) {
      pieces.push(end.parts[index]);
      const startToken = parseNumericToken(start.tokens[index]);
      const endToken = parseNumericToken(end.tokens[index]);
      if (!startToken || !endToken) {
        pieces.push(end.tokens[index]);
        continue;
      }
      const value = startToken.value + ((endToken.value - startToken.value) * clamped);
      pieces.push(formatNumericToken(value, endToken));
    }
    pieces.push(end.parts[end.parts.length - 1]);
    return pieces.join("");
  }

  function setAnimatedLineText(node, value) {
    const next = String(value || "");
    const current = node.dataset.currentText ?? node.textContent ?? "";
    cancelNumericAnimation(node);
    if (!current || current === next || !canAnimateNumericText(current, next)) {
      applyLineText(node, next);
      return;
    }
    const startedAt = performance.now();
    const step = (now) => {
      const progress = clamp((now - startedAt) / 360, 0, 1);
      applyLineText(node, interpolateNumericText(current, next, progress), { refresh: progress >= 1 });
      if (progress >= 1) {
        applyLineText(node, next);
        numericAnimations.delete(node);
        return;
      }
      numericAnimations.set(node, { raf: ctx.lifecycle.frame("session_view_numeric", step) });
    };
    numericAnimations.set(node, { raf: ctx.lifecycle.frame("session_view_numeric", step) });
  }

  function setText(root, field, value) {
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      const text = String(value || "");
      if (node.classList.contains("codex-usage-hud-line")) {
        node.title = text;
        if (field === "requestLine" || field === "requestLineExpanded") {
          setAnimatedLineText(node, text);
        } else {
          cancelNumericAnimation(node);
          applyLineText(node, text);
        }
        return;
      }
      node.textContent = text;
      if (text) node.title = text;
      else node.removeAttribute("title");
    });
  }

  function setFieldTitle(root, field, value) {
    const title = String(value || "");
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      if (title) node.title = title;
      else if (!node.dataset.copyable) node.removeAttribute("title");
    });
  }

  function fallbackCopyHudText(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.left = "-1000px";
    area.style.top = "0";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      ok = false;
    }
    area.remove();
    return ok;
  }

  async function copyHudText(text) {
    const value = String(text || "");
    if (!value) return false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }
    return fallbackCopyHudText(value);
  }

  function flashCopyState(node, ok) {
    const previousTitle = node.dataset.copyTitle || node.title || "";
    node.dataset.copied = ok ? "true" : "false";
    node.title = ok ? "已复制" : "复制失败";
    ctx.lifecycle.timeout("copy_feedback", () => {
      if (!node.isConnected) return;
      delete node.dataset.copied;
      node.title = previousTitle;
    }, 900);
  }

  function configureCopy(root, field, text, title, copyField) {
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      const value = String(text || "");
      if (value) {
        node.dataset.copyable = "true";
        node.dataset.copyText = value;
        node.dataset.copyTitle = title;
        node.dataset.copyField = copyField;
        node.title = title;
        return;
      }
      delete node.dataset.copyable;
      delete node.dataset.copyText;
      delete node.dataset.copyTitle;
      delete node.dataset.copyField;
      delete node.dataset.copied;
      node.removeAttribute("title");
    });
  }

  function elapsedSecondsText(startedAtMs, nowMs = Date.now()) {
    const seconds = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
    return `${seconds}s`.padStart(8, " ");
  }

  function refreshRunningRows() {
    const root = document.getElementById(rootId);
    if (!root) {
      ctx.lifecycle.clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return;
    }
    const nodes = Array.from(root.querySelectorAll(".codex-usage-hud-row-time[data-running='true']"));
    if (!nodes.length) {
      ctx.lifecycle.clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return;
    }
    const now = Date.now();
    for (const node of nodes) {
      const startedAtMs = Date.parse(node.dataset.startedAt || "");
      if (!Number.isFinite(startedAtMs)) continue;
      node.textContent = elapsedSecondsText(startedAtMs, now);
      node.dataset.tick = String(Math.floor(now / 1000) % 2);
    }
  }

  function syncRunningRowsTimer(root) {
    refreshRunningRows();
    const hasRunningRow = !!root.querySelector(".codex-usage-hud-row-time[data-running='true']");
    if (hasRunningRow && !window[runningTimerName]) {
      window[runningTimerName] = ctx.lifecycle.interval("session_view", refreshRunningRows, 1000);
    }
    if (!hasRunningRow && window[runningTimerName]) {
      ctx.lifecycle.clearInterval(window[runningTimerName]);
      window[runningTimerName] = 0;
    }
  }

  function appendStructuredRequestRow(list, item, index) {
    const row = document.createElement("div");
    row.className = "codex-usage-hud-row";
    row.dataset.latest = String(index === 0);
    const prefix = String(item?.prefix ?? "");
    const time = String(item?.time ?? "");
    const suffix = String(item?.suffix ?? "");
    if (!prefix && !suffix) {
      row.textContent = String(item?.text || "");
      list.appendChild(row);
      return;
    }
    const prefixNode = document.createElement("span");
    prefixNode.textContent = prefix;
    const timeNode = document.createElement("span");
    timeNode.className = "codex-usage-hud-row-time";
    timeNode.textContent = time;
    const running = index === 0 && item?.running && item?.startedAt;
    if (running) {
      timeNode.dataset.running = "true";
      timeNode.dataset.startedAt = String(item.startedAt);
    }
    const suffixNode = document.createElement("span");
    suffixNode.textContent = suffix;
    row.append(prefixNode, timeNode, suffixNode);
    list.appendChild(row);
  }

  function requestMoreRequestRows(list) {
    const total = Math.max(0, Number(list.dataset.requestRowsTotal || 0));
    const rendered = Math.max(0, Number(list.dataset.requestRowsRendered || 0));
    if (
      !total
      || rendered >= total
      || list.dataset.requestRowsLoading === "true"
      || !ctx.bindings.available(settingsCommandBindingName)
    ) return;
    list.dataset.requestRowsLoading = "true";
    try {
      ctx.bindings.send(settingsCommandBindingName, {
        id: `request-rows-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        createdAt: Date.now(),
        action: "loadMoreRequestRows",
        sessionId: String(list.dataset.requestRowsSessionId || ""),
      });
    } catch (_) {
      list.dataset.requestRowsLoading = "false";
    }
  }

  function bindRequestRowsPagination(list) {
    if (list.dataset.requestRowsPaginationBound === "true") return;
    list.dataset.requestRowsPaginationBound = "true";
    list.addEventListener("scroll", () => {
      if (list.scrollTop + list.clientHeight >= list.scrollHeight - 2) {
        requestMoreRequestRows(list);
      }
    }, { passive: true });
  }

  function renderRequestRows(root, rows, rowDetails, newSession = false, totalRows = 0, sessionId = "") {
    const list = root.querySelector('[data-field="requestRows"]');
    if (!list) return;
    const currentSessionId = String(sessionId || "");
    const previousScrollTop = list.dataset.requestRowsSessionId === currentSessionId
      ? list.scrollTop
      : 0;
    list.textContent = "";
    list.dataset.requestRowsSessionId = currentSessionId;
    list.dataset.requestRowsTotal = String(Math.max(0, Number(totalRows) || 0));
    list.dataset.requestRowsLoading = "false";
    const details = Array.isArray(rowDetails) && rowDetails.length ? rowDetails : [];
    if (details.length) {
      details.forEach((item, index) => appendStructuredRequestRow(list, item, index));
      list.dataset.requestRowsRendered = String(details.length);
      list.scrollTop = previousScrollTop;
      bindRequestRowsPagination(list);
      syncRunningRowsTimer(root);
      return;
    }
    if (newSession && (!Array.isArray(rows) || !rows.length)) {
      list.dataset.requestRowsRendered = "0";
      list.scrollTop = previousScrollTop;
      bindRequestRowsPagination(list);
      syncRunningRowsTimer(root);
      return;
    }
    const items = Array.isArray(rows) && rows.length ? rows : ["本次请求(等待) $0.0000 ↑- ◎- ↓- ◇- ↻- ∑-"];
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-row";
      row.textContent = String(item || "");
      list.appendChild(row);
    }
    list.dataset.requestRowsRendered = String(items.length);
    list.scrollTop = previousScrollTop;
    bindRequestRowsPagination(list);
    syncRunningRowsTimer(root);
  }

  function renderActiveSessionCandidates(root, payload) {
    const candidates = Array.isArray(payload?.matchCandidates)
      ? payload.matchCandidates.filter((item) => item && typeof item === "object")
      : [];
    const visible = payload?.followReason === "ambiguous-persisted-identity" && candidates.length > 1;
    root.querySelectorAll('[data-field="activeSessionCandidates"]').forEach((container) => {
      container.hidden = !visible;
      container.replaceChildren();
      if (!visible) return;

      const heading = document.createElement("div");
      heading.className = "codex-usage-hud-active-session-candidates-title";
      heading.textContent = "检测到多个未归档同名会话，请选择：";
      container.appendChild(heading);

      const detail = document.createElement("div");
      detail.className = "codex-usage-hud-active-session-candidates-detail";
      detail.textContent = "这些候选都来自当前会话列表；选择后将按精确会话 ID 绑定。";
      container.appendChild(detail);

      const list = document.createElement("div");
      list.className = "codex-usage-hud-active-session-candidates-list";
      candidates.forEach((candidate, index) => {
        const sessionId = String(candidate.sessionId || "").trim();
        if (!sessionId) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "codex-usage-hud-active-session-candidate";
        button.dataset.action = "active-session-candidate";
        button.dataset.sessionId = sessionId;
        button.dataset.selectionSeq = String(Number(payload?.selectionSeq || 0));
        const shortId = sessionId.length > 16
          ? `${sessionId.slice(0, 8)}…${sessionId.slice(-6)}`
          : sessionId;
        const name = String(candidate.rolloutName || "").trim();
        const updatedAtMs = Number(candidate.updatedAtMs || 0);
        const updated = updatedAtMs > 0
          ? new Date(updatedAtMs).toLocaleString()
          : "时间未知";
        button.title = `${shortId}${name ? `\n${name}` : ""}`;

        const label = document.createElement("span");
        label.className = "codex-usage-hud-active-session-candidate-label";
        label.textContent = `候选 ${index + 1} · ${shortId}`;
        const meta = document.createElement("span");
        meta.className = "codex-usage-hud-active-session-candidate-meta";
        meta.textContent = `${name || "本地会话文件未知"} · 更新于 ${updated}`;
        button.append(label, meta);
        list.append(button);
      });
      container.appendChild(list);
    });
  }

  function renderHeavyRounds(root, details) {
    const list = root.querySelector('[data-field="topHeavyRounds"]');
    if (!list) return;
    list.replaceChildren();
    const items = Array.isArray(details?.heavyRounds) ? details.heavyRounds.slice(0, 3) : [];
    if (!items.length) {
      list.dataset.empty = "true";
      const placeholders = [
        ["暂无会话高消耗轮次", "会话出现 token 确认后展示 Top 3"],
        ["等待统计", "不会因新需求开始而清空"],
        ["保持占位", "新轮次超过历史 Top 3 后刷新"],
      ];
      for (const [titleText, detailText] of placeholders) {
        const empty = document.createElement("div");
        empty.className = "codex-usage-hud-heavy-round";
        empty.dataset.placeholder = "true";
        empty.innerHTML = `
          <span class="codex-usage-hud-heavy-round-title">${titleText}</span>
          <span class="codex-usage-hud-heavy-round-detail">${detailText}</span>
        `;
        list.appendChild(empty);
      }
      return;
    }
    delete list.dataset.empty;
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-heavy-round";
      const tooltip = String(item.tooltip || [item.title, item.detail].filter(Boolean).join("  "));
      if (tooltip) row.title = tooltip;
      const copyText = String(item.copyText || "");
      if (copyText) {
        row.dataset.copyable = "true";
        row.dataset.copyText = copyText;
        row.dataset.copyTitle = tooltip ? `点击复制轮次内容\n${tooltip}` : "点击复制轮次内容";
        row.dataset.copyField = "heavy";
        row.title = row.dataset.copyTitle;
      }
      const title = document.createElement("span");
      title.className = "codex-usage-hud-heavy-round-title";
      title.textContent = String(item.title || "");
      const detail = document.createElement("span");
      detail.className = "codex-usage-hud-heavy-round-detail";
      detail.textContent = String(item.detail || "");
      if (tooltip) detail.title = tooltip;
      row.append(title, detail);
      list.appendChild(row);
    }
  }

  function renderActivityTimeline(root, details) {
    const list = root.querySelector('[data-field="topActivityTrail"]');
    if (!list) return;
    const previousScrollTop = list.scrollTop || 0;
    const button = root.querySelector('[data-field="topActivityLoadMore"]');
    const items = Array.isArray(details?.activityTrail) ? details.activityTrail : [];
    const allItems = items.filter((item) => item && (item.title || item.detail || item.time));
    const signature = allItems.map((item) => [item.time, item.title, item.detail, item.tooltip].join("|")).join(";");
    const context = [
      details?.taskOrdinal || "",
      details?.currentTask || "",
      details?.title || "",
    ].join("|");
    const contextChanged = list.dataset.context !== context;
    if (contextChanged) {
      list.dataset.context = context;
      list.dataset.visibleCount = "4";
      list.scrollTop = 0;
    } else if (!list.dataset.visibleCount) {
      list.dataset.visibleCount = "4";
    }
    list.dataset.signature = signature;
    const visibleCount = Math.max(4, Number(list.dataset.visibleCount || 4));
    const visibleItems = allItems.slice(0, visibleCount);
    list.dataset.fill = visibleItems.length > 0 && visibleItems.length <= 4 ? "spread" : "dense";
    list.replaceChildren();
    if (button) {
      button.hidden = false;
      button.disabled = visibleItems.length >= allItems.length;
      button.textContent = "查看更多";
      button.title = visibleItems.length >= allItems.length ? "已显示全部活动轨迹" : "加载更早的活动轨迹";
    }
    if (!visibleItems.length) {
      const empty = document.createElement("div");
      empty.className = "codex-usage-hud-activity-node";
      empty.title = "等待会话产生新活动";
      empty.innerHTML = `
        <span class="codex-usage-hud-activity-node-time">--:--</span>
        <span class="codex-usage-hud-activity-node-dot"></span>
        <span>
          <span class="codex-usage-hud-activity-node-title">暂无时间节点</span>
          <span class="codex-usage-hud-activity-node-detail">等待会话产生新活动</span>
        </span>
      `;
      list.appendChild(empty);
      return;
    }
    for (const item of visibleItems) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-activity-node";
      row.dataset.active = String(!!item.active);
      const tooltip = String(item.tooltip || [item.time, item.title, item.detail].filter(Boolean).join("  "));
      if (tooltip) row.title = tooltip;
      const time = document.createElement("span");
      time.className = "codex-usage-hud-activity-node-time";
      time.textContent = String(item.time || "--:--");
      const dot = document.createElement("span");
      dot.className = "codex-usage-hud-activity-node-dot";
      const body = document.createElement("span");
      const title = document.createElement("span");
      title.className = "codex-usage-hud-activity-node-title";
      title.textContent = String(item.title || "活动");
      title.title = tooltip || title.textContent;
      const detail = document.createElement("span");
      detail.className = "codex-usage-hud-activity-node-detail";
      detail.textContent = String(item.detail || "");
      detail.title = tooltip || detail.textContent;
      if (detail.textContent || tooltip) {
        detail.dataset.copyable = "true";
        detail.dataset.copyText = tooltip || detail.textContent;
        detail.dataset.copyTitle = "点击复制轨迹详情";
        detail.dataset.copyField = "trail";
        detail.title = tooltip ? `点击复制轨迹详情\n${tooltip}` : "点击复制轨迹详情";
      }
      body.append(title, detail);
      row.append(time, dot, body);
      list.appendChild(row);
    }
    if (!contextChanged) {
      list.scrollTop = previousScrollTop;
    }
  }

  function renderTopDetails(root, payload) {
    const details = payload?.topDetails || {};
    const copies = payload?.topCopies || {};
    const mapping = {
      topTitle: details.title || "Codex 会话 / 预算",
      topSession: details.session || "",
      topSessionCost: details.sessionCost || "",
      topSessionTokens: details.sessionTokens || "",
      topSessionRounds: details.sessionRounds || "",
      topTaskOrdinalSession: details.taskOrdinalSession || "",
      topTaskOrdinalActivity: details.taskOrdinalActivity || "",
      topCacheText: details.cacheText || "",
      topSessionMix: details.sessionMix || "",
      topSessionAverage: details.sessionAverage || "",
      topSessionComposition: details.sessionComposition || "",
      topHeavyRoundsSummary: details.heavyRoundsSummary || "",
      topSessionInputTokens: details.sessionInputTokens || "",
      topSessionCachedTokens: details.sessionCachedTokens || "",
      topSessionOutputTokens: details.sessionOutputTokens || "",
      topSessionReasoningTokens: details.sessionReasoningTokens || "",
      topWarnings: details.warnings || "",
      topExecutingLabel: details.executingLabel || "正在执行",
      topExecuting: details.executing || "",
      topCurrentTaskLabel: details.currentTaskLabel || "当前需求",
      topCurrentTask: details.currentTask || "",
      topActivityState: details.activityState || "",
      topActivityElapsedLabel: details.activityElapsedLabel || "已运行",
      topActivityElapsed: details.activityElapsed || "",
      topActivityGapLabel: details.activityGapLabel || "当前等待",
      topActivityGap: details.activityGap || "",
      topActivityLastLabel: details.activityLastLabel || "需求轮次",
      topActivityLast: details.activityLast || "",
      topSlow: details.slow || "",
      topGap: details.gap || "",
    };
    for (const [field, value] of Object.entries(mapping)) setText(root, field, value);
    setFieldTitle(root, "topActivityLast", details.activityLastTooltip || details.activityLast || "");
    renderHeavyRounds(root, details);
    renderActivityTimeline(root, details);
    const hasWarnings = !!String(details.warnings || "").trim();
    root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
      node.hidden = !hasWarnings;
    });
    budgetDomain.apply(root, payload || {});
    configureCopy(root, "topSlow", copies.slow || "", "点击复制最慢工具命令", "slow");
    configureCopy(root, "topGap", copies.gap || "", "点击复制最长等待详情", "gap");
    configureCopy(root, "topCurrentTask", details.currentTask || "", `点击复制当前需求\n${details.currentTask || ""}`, "task");
    configureCopy(root, "topExecuting", details.executing || "", `点击复制${details.executingLabel || "当前活动"}\n${details.executing || ""}`, "executing");
  }
  function applyCurrentSessionPayload(root, payload) {
    setText(root, "topLine", payload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", payload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", payload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!payload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, payload?.requestStatus === "error");
    });
    renderTopDetails(root, payload || {});
    renderRequestRows(
      root,
      payload?.requestRows || [],
      payload?.requestRowDetails || [],
      !!(payload?.newSession || payload?.pendingSession),
      payload?.requestRowsTotal || 0,
      payload?.rendererSessionId || payload?.sessionId || "",
    );
    renderActiveSessionCandidates(root, payload || {});
    renderBackgroundUsageNotification(root, payload || {});
    diagnosticsDomain.applyConnectionHealth(root, payload || {});
    applyActiveSessionSequence(payload);
  }
  function applyActiveSessionSequence(payload) {
    if (payload?.cachedPreview) return;
    const appliedSeq = Number(payload?.selectionSeq || 0);
    if (appliedSeq > Number(window[activeSessionAppliedSeqName] || 0)) {
      window[activeSessionAppliedSeqName] = appliedSeq;
    }
    if (appliedSeq > Number(window[activeSessionSelectionSeqName] || 0)) {
      // A HUD reinjection can receive an observation from the previous script
      // realm before installing the new one. Keep the next click monotonic
      // relative to the Python tracker that already accepted that sequence.
      window[activeSessionSelectionSeqName] = appliedSeq;
    }
  }

  function applySessionSwitchPayload(root, payload) {
    setText(root, "topLine", payload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", payload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", payload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!payload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, payload?.requestStatus === "error");
    });
    renderActiveSessionCandidates(root, payload || {});
    renderBackgroundUsageNotification(root, payload || {});
    diagnosticsDomain.applyConnectionHealth(root, payload || {});
    applyActiveSessionSequence(payload);
  }

    let installed = false;
    const numericTokenRe = /\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[kM%])?/g;
    const numericAnimations = new WeakMap();

    function install() {
      if (installed) return false;
      installed = true;
      return true;
    }

    function apply(root, payload, kind = "currentSession") {
      if (!installed) install();
      if (kind === "sessionSwitch") applySessionSwitchPayload(root, payload || {});
      else applyCurrentSessionPayload(root, payload || {});
    }

    function dispose() {
      const wasInstalled = installed;
      installed = false;
      const root = document.getElementById(rootId);
      root?.querySelectorAll(".codex-usage-hud-line").forEach(cancelNumericAnimation);
      ctx.lifecycle.clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return wasInstalled;
    }

    return {
      install,
      apply,
      dispose,
      cancelNumericAnimation,
      configureCopy,
      copyHudText,
      flashCopyState,
      refreshAllMarquees,
      refreshMarquee,
      renderActivityTimeline,
      setFieldTitle,
      setText,
    };
  }

  const sessionViewDomain = ctx.domains.register(
    "session_view",
    createSessionViewDomain(ctx, shared),
  );
  const {
    cancelNumericAnimation,
    configureCopy,
    copyHudText,
    flashCopyState,
    refreshAllMarquees,
    refreshMarquee,
    renderActivityTimeline,
    setFieldTitle,
    setText,
  } = sessionViewDomain;
"""

__all__ = ["TEXT"]
