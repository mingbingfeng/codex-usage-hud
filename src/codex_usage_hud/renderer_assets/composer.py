"""Renderer composer domain asset."""

TEXT = r"""
  function createComposerDomain(ctx, shared) {
      function composerElement() {
        if (anchorUsable(cachedComposerNode)) return cachedComposerNode;
        cachedComposerNode = null;
        const candidates = new Set();
        const composerClasses = [
          "relative",
          "flex",
          "flex-col",
          "bg-token-input-background/90",
        ];
        Array.from(document.querySelectorAll("div")).forEach((node) => {
          if (hasAllClasses(node, composerClasses) && visible(node)) candidates.add(node);
        });
        Array.from(document.querySelectorAll(".composer-footer")).forEach((footer) => {
          if (!visible(footer)) return;
          let node = footer;
          for (let depth = 0; node instanceof HTMLElement && depth < 7; depth += 1, node = node.parentElement) {
            if (visible(node)) candidates.add(node);
          }
        });
        Array.from(document.querySelectorAll("textarea, [contenteditable='true']")).forEach((input) => {
          if (!visible(input)) return;
          let node = input;
          for (let depth = 0; node instanceof HTMLElement && depth < 7; depth += 1, node = node.parentElement) {
            if (visible(node)) candidates.add(node);
          }
        });
        const best = Array.from(candidates)
          .map((node, index) => ({ node, index, score: scoreComposer(node) }))
          .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node;
        cachedComposerNode = anchorUsable(best) ? best : null;
        return cachedComposerNode;
      }

      function composerRect() {
        const best = composerElement();
        return visible(best) ? best.getBoundingClientRect() : null;
      }

      function composerInputElement() {
        const composer = composerElement();
        if (!composer) return null;
        const input = composer.querySelector("textarea, [contenteditable='true'], [role='textbox']");
        return visible(input) ? input : null;
      }

      function composerInputText(input) {
        if (!input) return "";
        if (input.tagName === "TEXTAREA" || typeof input.value === "string") {
          return String(input.value || "");
        }
        return String(input.innerText || input.textContent || "");
      }

      function composerTokenCount(text) {
        const normalized = String(text || "").replace(/\r\n/g, "\n");
        // Count by Unicode code points so CJK characters and emoji each read as one.
        return Array.from(normalized).length;
      }

      // ── 输入框附件采集 ──────────────────────────────────────────────
      // 通过 CDP 实测确认的选择器（见 docs/token_badge_integration.md）：
      //   图片：div.composer-attachment-surface[aria-label] 内含 <img>，取 naturalWidth/Height
      //   文件：button[aria-label]（class 含 group-hover/file-attachment），取文件名
      //   @引用/技能：span.inline-mention-brand-aware 或 chip/pill 文本，按 @ / $ 前缀分类
      function collectComposerAttachments() {
        if (!composerBadgeEnabled) {
          return { images: [], files: [], mentions: [], skills: [] };
        }
        const composer = composerElement();
        const empty = { images: [], files: [], mentions: [], skills: [] };
        if (!composer) return empty;

        const images = [];
        const collectImageAttachment = (surface) => {
          const img = surface.querySelector("img");
          if (!img) return;
          const name = String(surface.getAttribute("aria-label") || img.getAttribute("alt") || "").trim();
          images.push({
            name,
            width: Math.max(0, Math.round(Number(img.naturalWidth) || 0)),
            height: Math.max(0, Math.round(Number(img.naturalHeight) || 0)),
          });
        };
        composer.querySelectorAll("div.composer-attachment-surface").forEach(collectImageAttachment);
        composer.querySelectorAll("img[src^='blob:'], img[src^='data:image']").forEach((img) => {
          if (img.closest("div.composer-attachment-surface")) return;
          const surface = img.closest("[aria-label], button, [role='button'], div") || img.parentElement || img;
          collectImageAttachment(surface);
        });

        const files = [];
        const normalizeFileAttachmentName = (label) => {
          let value = normalize(label || "");
          value = value.replace(/^(remove|delete|open|preview|download)\s+(file|attachment)\s+/i, "");
          value = value.replace(/^(remove|delete|open|preview|download)\s+/i, "");
          value = value.replace(/^(file|attachment):?\s+/i, "");
          return value.trim();
        };
        // Codex 文件引用 chip 的 React fiber 暴露 resourcePath（绝对路径），
        // 桌面/仓库外文件靠文件名根本搜不到，必须直接拿这个路径读盘才准。
        // mention 场景（inline `@file` chip）是 ProseMirror 节点，路径挂在 pmViewDesc.node.attrs 上。
        const readFiberFilePath = (node) => {
          if (!node) return "";
          // 先看 ProseMirror atMention 节点（span.inline-mention-brand-aware）：
          // node.pmViewDesc.node.attrs 里通常有 { label, path, fsPath }，path 是相对项目根的路径。
          const pmDesc = node.pmViewDesc;
          if (pmDesc && pmDesc.node && pmDesc.node.attrs) {
            const attrs = pmDesc.node.attrs;
            for (const key of ["fsPath", "path", "absolutePath", "resourcePath"]) {
              const value = attrs[key];
              if (typeof value === "string" && value.trim()) return value.trim();
            }
          }
          const fiberKey = Object.keys(node).find((k) => k.startsWith("__reactFiber$"));
          let fiber = fiberKey ? node[fiberKey] : null;
          for (let depth = 0; fiber && depth < 15; depth += 1, fiber = fiber.return) {
            const props = fiber.memoizedProps;
            if (!props || typeof props !== "object") continue;
            for (const key of ["resourcePath", "localPath", "absolutePath", "filePath"]) {
              const value = props[key];
              if (typeof value === "string" && value.trim()) return value.trim();
            }
          }
          return "";
        };
        composer.querySelectorAll("button[aria-label]").forEach((button) => {
          if (!/file-attachment/.test(String(button.className || ""))) return;
          const name = normalizeFileAttachmentName(button.getAttribute("aria-label"));
          if (!name) return;
          const path = readFiberFilePath(button);
          files.push(path ? { name, path } : name);
        });

        const mentions = [];
        const mentionSeen = new Set();
        const skills = [];
        const looksLikeFileReferenceText = (value) => (
          /^[\w.() -]+\.[A-Za-z0-9]{1,12}$/.test(value)
          || /^\[[^\]]+\]\([^)]+\)$/.test(value)
          || /[/\\][^/\\]+\.[A-Za-z0-9]{1,12}$/.test(value)
        );
        // 复制 mention chip 出来是 `[name](abs-path)`，直接解析出绝对路径最省事；
        // 页面里 mention 还只是 chip 元素，无 markdown 文本，靠 fiber 兜底。
        const parseMarkdownFileRef = (value) => {
          const match = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(String(value || "").trim());
          if (!match) return null;
          const name = match[1].trim();
          const path = match[2].trim();
          return name ? { name, path } : null;
        };
        const pushMentionEntry = (name, path) => {
          const cleanName = String(name || "").trim();
          if (!cleanName) return;
          const cleanPath = String(path || "").trim();
          const key = `${cleanName}|${cleanPath}`;
          if (mentionSeen.has(key)) return;
          mentionSeen.add(key);
          mentions.push(cleanPath ? { name: cleanName, path: cleanPath } : cleanName);
        };
        const addMentionOrSkill = (text, defaultKind = "", fiberPath = "") => {
          const raw = normalize(text || "");
          if (!raw) return;
          const parsed = parseMarkdownFileRef(raw);
          if (parsed) {
            pushMentionEntry(parsed.name, parsed.path);
            return;
          }
          if (raw.startsWith("$")) {
            if (!skills.includes(raw)) skills.push(raw);
          } else if (raw.startsWith("@")) {
            pushMentionEntry(raw, fiberPath);
          } else if (defaultKind === "mention" && looksLikeFileReferenceText(raw)) {
            pushMentionEntry(raw, fiberPath);
          }
        };
        const collectMentionOrSkillText = (node) => {
          if (!node || node.closest?.(`#${rootId}`)) return;
          // 带图标的工具开关（浏览器/电脑等）是 composer 控件装饰，不进 prompt，跳过。
          const text = normalize(node.textContent || "");
          const defaultKind = looksLikeFileReferenceText(text) ? "mention" : "";
          if (node.querySelector?.("img, svg") && !/^[@$]/.test(text) && defaultKind !== "mention") return;
          // mention chip 的 React fiber 与 file-attachment 一样带 resourcePath，
          // 只有拿到绝对路径才能让 Python 端跨仓库读盘算真实 token（否则退化成按名估算）。
          const fiberPath = readFiberFilePath(node);
          addMentionOrSkill(node.getAttribute?.("aria-label"), defaultKind, fiberPath);
          addMentionOrSkill(node.getAttribute?.("title"), defaultKind, fiberPath);
          addMentionOrSkill(text, defaultKind, fiberPath);
        };
        composer.querySelectorAll([
          "span.inline-mention-brand-aware",
          "[data-testid*='mention']",
          "[data-testid*='skill']",
          "[class*='mention']",
          "[class*='skill']",
          "[aria-label^='@']",
          "[aria-label^='$']",
        ].join(",")).forEach(collectMentionOrSkillText);

        return { images, files, mentions, skills };
      }

      function composerAttachmentsSignature(attachments) {
        return JSON.stringify([
          attachments.images.map((item) => `${item.name}|${item.width}x${item.height}`),
          attachments.files.map((item) =>
            (item && typeof item === "object") ? `${item.name}|${item.path || ""}` : String(item)
          ),
          attachments.mentions.map((item) =>
            (item && typeof item === "object") ? `${item.name}|${item.path || ""}` : String(item)
          ),
          attachments.skills,
        ]);
      }

      function reportComposerAttachments(force = false) {
        if (!composerBadgeEnabled) return;
        const attachments = collectComposerAttachments();
        const signature = composerAttachmentsSignature(attachments);
        if (!force && window[composerAttachmentsSignatureName] === signature) return;
        window[composerAttachmentsSignatureName] = signature;
        const ref = readActiveSessionRef();
        const payload = {
          sessionId: ref.sessionId || "",
          images: attachments.images,
          files: attachments.files,
          mentions: attachments.mentions,
          skills: attachments.skills,
          observedAt: Date.now(),
        };
        // 页面 CSP 的 connect-src 不含 http://127.0.0.1，fetch 到本地桥会被拦，
        // 因此优先走 CDP binding（与 active-session 一致），fetch 仅作兜底。
        if (ctx.bindings.available(composerAttachmentsBindingName)) {
          try {
            ctx.bindings.send(composerAttachmentsBindingName, payload);
            return;
          } catch (_) {}
        }
        const bridge = settingsBridgeUrl();
        if (!bridge) return;
        fetch(`${bridge}/composer-attachments`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true,
        }).catch(() => {});
      }

      function scheduleComposerAttachmentsReport(force = false) {
        if (!composerBadgeEnabled) return;
        ctx.lifecycle.clearTimeout(window[composerAttachmentsTimerName] || 0);
        window[composerAttachmentsTimerName] = ctx.lifecycle.timeout("composer_attachments", () => {
          reportComposerAttachments(force);
        }, composerAttachmentsDebounceMs);
      }

      function collectLayoutSnapshot() {
        const states = loadStates();
        const panels = {};
        for (const name of Object.keys(PANEL)) {
          const state = states[name] || {};
          const node = document.querySelector(`#${rootId} [data-panel="${name}"]`);
          const rect = node ? node.getBoundingClientRect() : null;
          panels[name] = {
            expanded: !!state.expanded,
            manual: !!state.manual,
            x: Number.isFinite(state.x) ? Math.round(state.x) : (rect ? Math.round(rect.left) : null),
            y: Number.isFinite(state.y) ? Math.round(state.y) : (rect ? Math.round(rect.top) : null),
            width: Number.isFinite(state.width) ? Math.round(state.width) : (rect ? Math.round(rect.width) : null),
            collapsedHeight: Number.isFinite(state.collapsedHeight) ? Math.round(state.collapsedHeight) : null,
            expandedHeight: Number.isFinite(state.expandedHeight) ? Math.round(state.expandedHeight) : null,
          };
        }
        return {
          viewport: { width: window.innerWidth, height: window.innerHeight },
          panels,
        };
      }

      function reportLayout(reason, panelName) {
        const snapshot = collectLayoutSnapshot();
        const signature = JSON.stringify([reason, panelName || "", snapshot.panels]);
        if (window[layoutReportSignatureName] === signature) return;
        window[layoutReportSignatureName] = signature;
        const payload = {
          reason: String(reason || ""),
          panel: String(panelName || ""),
          layout: snapshot,
          observedAt: Date.now(),
        };
        if (ctx.bindings.available(layoutBindingName)) {
          try {
            ctx.bindings.send(layoutBindingName, payload);
            return;
          } catch (_) {}
        }
        const bridge = settingsBridgeUrl();
        if (!bridge) return;
        fetch(`${bridge}/layout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true,
        }).catch(() => {});
      }

      function scheduleLayoutReport(reason, panelName) {
        ctx.lifecycle.clearTimeout(window[layoutReportTimerName] || 0);
        window[layoutReportTimerName] = ctx.lifecycle.timeout("layout_report", () => {
          reportLayout(reason, panelName);
        }, 80);
      }

      function humanizeTokens(value) {
        const n = Math.max(0, Math.round(Number(value) || 0));
        if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
        if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
        return String(n);
      }

      function currentPayload() {
        const payload = ctx.state.payload();
        if (Array.isArray(payload.supportImages) && payload.supportImages.length) {
          return payload;
        }
        const persistedSupportImages = loadPersistedSupportImages();
        if (!persistedSupportImages.length) return payload;
        return { ...payload, supportImages: persistedSupportImages };
      }

      function formatMoney3(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
        return `$${(Number(value) || 0).toFixed(3)}`;
      }

      function updateComposerBadgeText(root = document.getElementById(rootId)) {
        window[composerBadgeRafName] = 0;
        if (!composerBadgeEnabled || !root) return;
        const payload = currentPayload();
        // 运行中：黄灯优先，滚动显示 AI 正在读取的文件。
        if (payload.activityWarning && payload.activityReadingFile) {
          setText(root, "requestComposerTokens", String(payload.activityReadingFile));
          return;
        }
        // 未发送：静态底价（会话/规则/工具/协议，来自 Python）+ 实时输入文本（浏览器侧）。
        const base = Math.max(0, Number(payload.preSendBaseTokens) || 0);
        const input = window[composerInputNodeName];
        const live = composerTokenCount(composerInputText(input));
        const totalTokens = base + live;
        // 合计金额 = Python 预算(不含实时输入) + 实时输入 × input 单价。
        const hasPrices = !!payload.preSendHasPrices;
        const liveCost = live * (Number(payload.preSendInputPrice) || 0);
        const totalCost = hasPrices ? (Number(payload.preSendTotalCost) || 0) + liveCost : null;
        const money = hasPrices ? formatMoney3(totalCost) : "";
        const badgeText = money
          ? `预估 ${money} · ${humanizeTokens(totalTokens)} Ts`
          : `预估 ~${humanizeTokens(totalTokens)} Ts`;
        setText(root, "requestComposerTokens", badgeText);
        renderComposerBreakdown(root, payload, live, totalTokens, totalCost);
      }

      function scheduleComposerBadgeUpdate() {
        if (!composerBadgeEnabled) return;
        if (window[composerBadgeRafName]) return;
        window[composerBadgeRafName] = ctx.frames.schedule("composer_badge", () => updateComposerBadgeText());
      }

      function renderComposerBreakdown(root, payload, liveInputTokens, totalTokens, totalCost) {
        if (!composerBadgeEnabled) return;
        const node = root.querySelector('[data-field="requestComposerBreakdown"]');
        if (!node) return;
        const rows = Array.isArray(payload.preSendBreakdown) ? payload.preSendBreakdown : [];
        if (!rows.length) {
          node.innerHTML = "";
          hideComposerBreakdown(root);
          return;
        }
        const hasPrices = !!payload.preSendHasPrices;
        const inputPrice = Number(payload.preSendInputPrice) || 0;
        const rowHtml = rows.map((row, index) => {
          // 第一行「输入框内容」用浏览器侧实时值覆盖 Python 的占位（token 与金额都实时）。
          const isLiveInput = index === 0;
          const tokens = isLiveInput ? liveInputTokens : (Number(row?.tokens) || 0);
          const cost = isLiveInput
            ? (hasPrices ? tokens * inputPrice : null)
            : (row?.cost ?? null);
          const label = escapeHtml(String(row?.label || ""));
          const note = row?.note ? `<span class="codex-usage-hud-token-breakdown-note">${escapeHtml(String(row.note))}</span>` : "";
          const money = hasPrices ? escapeHtml(formatMoney3(cost)) : "—";
          return `<span class="codex-usage-hud-token-breakdown-row">`
            + `<span class="codex-usage-hud-token-breakdown-name">${label}${note}</span>`
            + `<span class="codex-usage-hud-token-breakdown-tok">${escapeHtml(humanizeTokens(tokens))} Ts</span>`
            + `<span class="codex-usage-hud-token-breakdown-cost">${money}</span>`
            + `</span>`;
        }).join("");
        const titleMoney = hasPrices ? formatMoney3(totalCost) : "";
        const titleHtml = `<div class="codex-usage-hud-token-breakdown-title">`
          + `<span>发送底价预估</span>`
          + `<span class="codex-usage-hud-token-breakdown-title-cost">${escapeHtml(titleMoney)}</span>`
          + `</div>`;
        const footHtml = `<span class="codex-usage-hud-token-breakdown-row" data-total="true">`
          + `<span class="codex-usage-hud-token-breakdown-name">合计</span>`
          + `<span class="codex-usage-hud-token-breakdown-tok">${escapeHtml(humanizeTokens(totalTokens))} Ts</span>`
          + `<span class="codex-usage-hud-token-breakdown-cost">${hasPrices ? escapeHtml(formatMoney3(totalCost)) : "—"}</span>`
          + `</span>`;
        node.innerHTML = titleHtml + rowHtml + footHtml;
        // 若正处于展开态，内容刷新后重新定位（打字时金额/合计会变）。
        if (node.dataset.open === "true") positionComposerBreakdown(root, node);
      }

      function composerBadgeElement(root = document.getElementById(rootId)) {
        if (!composerBadgeEnabled) return null;
        return root?.querySelector(".codex-usage-hud-token-badge") || null;
      }

      function positionComposerBreakdown(root, node) {
        const badge = composerBadgeElement(root);
        if (!badge) return;
        const rect = badge.getBoundingClientRect();
        // 先显示以取得尺寸，再定位到徽章正上方、右对齐。
        const width = node.offsetWidth || 200;
        const height = node.offsetHeight || 80;
        const margin = 6;
        let left = rect.right - width;
        let top = rect.top - height - margin;
        if (top < 4) top = rect.bottom + margin;         // 顶部空间不足则翻到下方
        if (left < 4) left = 4;                            // 防止溢出左边界
        const maxLeft = window.innerWidth - width - 4;
        if (left > maxLeft) left = Math.max(4, maxLeft);
        node.style.left = `${Math.round(left)}px`;
        node.style.top = `${Math.round(top)}px`;
      }

      function showComposerBreakdown(root = document.getElementById(rootId)) {
        if (!composerBadgeEnabled) return;
        if (!root) return;
        if (badgeWarningActive()) return;                  // 黄灯态不显示底价明细
        const node = root.querySelector('[data-field="requestComposerBreakdown"]');
        if (!node || !node.innerHTML) return;
        node.hidden = false;
        node.dataset.open = "true";
        positionComposerBreakdown(root, node);
      }

      function hideComposerBreakdown(root = document.getElementById(rootId)) {
        if (!composerBadgeEnabled) return;
        const node = root?.querySelector('[data-field="requestComposerBreakdown"]');
        if (!node) return;
        node.dataset.open = "false";
        node.hidden = true;
      }

      function badgeWarningActive() {
        const payload = currentPayload();
        return !!(payload.activityWarning && payload.activityReadingFile);
      }

      function refreshComposerBadgeState(root = document.getElementById(rootId)) {
        if (!composerBadgeEnabled) return;
        if (!root) return;
        const warning = badgeWarningActive();
        const focused = !!window[composerFocusStateName];
        root.querySelectorAll('[data-composer-badge]').forEach((node) => {
          if (warning) {
            node.dataset.badgeState = "warning";
            node.dataset.composerBadge = "active";
          } else {
            delete node.dataset.badgeState;
            node.dataset.composerBadge = focused ? "active" : "idle";
          }
        });
        // 黄灯运行时或输入框聚焦时都需要刷新文案。
        if (warning || focused) scheduleComposerBadgeUpdate();
        // 徽章隐藏（idle）或黄灯态时，强制收起底价明细浮层。
        if (warning || !focused) hideComposerBreakdown(root);
      }

      function setComposerBadgeActive(active) {
        if (!composerBadgeEnabled) return;
        const root = document.getElementById(rootId);
        if (!root) return;
        const changed = window[composerFocusStateName] !== !!active;
        window[composerFocusStateName] = !!active;
        refreshComposerBadgeState(root);
        // Showing/hiding the badge changes the marquee line's available width, so
        // re-evaluate scrolling without disturbing the marquee logic itself.
        if (changed) ctx.lifecycle.frame("composer", () => refreshAllMarquees(root));
      }

      function detachComposerInputWatchers() {
        ctx.lifecycle.disposeScope("composer_input");
        window[composerInputNodeName] = null;
        window[composerInputHandlersName] = null;
        ctx.observers.clear("composer_attachments");
        window[composerAttachmentsObserverName] = null;
      }

      function ensureComposerInputWatchers() {
        if (!composerBadgeEnabled) {
          detachComposerInputWatchers();
          return;
        }
        const input = composerInputElement();
        const composer = composerElement();
        const existingHandlers = window[composerInputHandlersName];
        if (input === window[composerInputNodeName] && composer && existingHandlers?.composer === composer) {
          if (input && window[composerFocusStateName]) scheduleComposerBadgeUpdate();
          return;
        }
        detachComposerInputWatchers();
        if (!input) {
          setComposerBadgeActive(false);
          return;
        }
        const handlers = {
          focus: () => setComposerBadgeActive(true),
          blur: () => setComposerBadgeActive(false),
          input: () => {
            if (window[composerFocusStateName]) scheduleComposerBadgeUpdate();
            // 粘贴/删除文本也可能带走 @ 引用，顺带核对附件。
            scheduleComposerAttachmentsReport();
          },
          keydown: (event) => {
            if (
              event.defaultPrevented
              || event.isComposing
              || event.key !== "Enter"
              || event.shiftKey
              || event.altKey
              || event.ctrlKey
              || event.metaKey
            ) {
              return;
            }
            scheduleActiveSessionSendFollowup("composer-enter");
          },
          click: (event) => {
            const button = event.target?.closest?.("button, [role='button']");
            if (!button || !composer?.contains?.(button)) return;
            if (!activeSessionComposerSubmitButton(button)) return;
            scheduleActiveSessionSendFollowup("composer-send-click");
          },
          composer,
        };
        const composerScope = ctx.lifecycle.scope("composer_input");
        composerScope.listen(input, "focus", handlers.focus, true);
        composerScope.listen(input, "blur", handlers.blur, true);
        composerScope.listen(input, "input", handlers.input, true);
        composerScope.listen(input, "keydown", handlers.keydown, true);
        window[composerInputNodeName] = input;
        window[composerInputHandlersName] = handlers;
        // The composer may already hold focus when we (re)attach after a re-inject.
        const focused = document.activeElement === input
          || (input.contains?.(document.activeElement) ?? false);
        setComposerBadgeActive(focused);
        // 附件（图片/文件/@引用）是异步插入的 DOM 节点，靠 MutationObserver 捕获增删。
        if (composer) {
          composerScope.listen(composer, "click", handlers.click, true);
          const observer = ctx.observers.set("composer_attachments", new MutationObserver(() => scheduleComposerAttachmentsReport()));
          observer.observe(composer, { subtree: true, childList: true });
          window[composerAttachmentsObserverName] = observer;
        }
        // 首次挂载立即上报一次（可能已带附件，例如重注入后）。
        scheduleComposerAttachmentsReport(true);
      }

    function install() {
      return true;
    }

    function apply(_root, _payload) {
      return true;
    }

    function dispose() {
      return true;
    }

    return {
      install,
      apply,
      dispose,
      composerElement,
      composerRect,
      composerInputElement,
      composerInputText,
      composerTokenCount,
      collectComposerAttachments,
      composerAttachmentsSignature,
      reportComposerAttachments,
      scheduleComposerAttachmentsReport,
      collectLayoutSnapshot,
      reportLayout,
      scheduleLayoutReport,
      humanizeTokens,
      formatMoney3,
      updateComposerBadgeText,
      scheduleComposerBadgeUpdate,
      renderComposerBreakdown,
      composerBadgeElement,
      positionComposerBreakdown,
      showComposerBreakdown,
      hideComposerBreakdown,
      badgeWarningActive,
      refreshComposerBadgeState,
      setComposerBadgeActive,
      detachComposerInputWatchers,
      ensureComposerInputWatchers,
    };
  }

  const composerDomain = ctx.domains.register(
    "composer",
    createComposerDomain(ctx, shared),
  );
  const {
    composerElement,
    composerRect,
    composerInputElement,
    composerInputText,
    composerTokenCount,
    collectComposerAttachments,
    composerAttachmentsSignature,
    reportComposerAttachments,
    scheduleComposerAttachmentsReport,
    collectLayoutSnapshot,
    reportLayout,
    scheduleLayoutReport,
    humanizeTokens,
    formatMoney3,
    updateComposerBadgeText,
    scheduleComposerBadgeUpdate,
    renderComposerBreakdown,
    composerBadgeElement,
    positionComposerBreakdown,
    showComposerBreakdown,
    hideComposerBreakdown,
    badgeWarningActive,
    refreshComposerBadgeState,
    setComposerBadgeActive,
    detachComposerInputWatchers,
    ensureComposerInputWatchers,
  } = composerDomain;
"""

__all__ = ["TEXT"]
