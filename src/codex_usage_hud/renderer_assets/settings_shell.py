"""Renderer settings shell domain asset."""

from .settings_support_panels import TEXT as SETTINGS_SUPPORT_PANELS

_TEXT_PREFIX = r"""
  function createSettingsShellDomain(ctx, shared) {
      const pricingWorkflowState = {
        pendingSettings: null,
        pendingChanges: [],
        impactPreview: null,
        impactEffectiveAt: "",
        impactRequestId: "",
        importPayload: null,
        importSourcePayload: null,
        importDefaultEffectiveAt: "",
        importPreview: null,
        importMode: "paste",
        recalculationScope: null,
        recalculationPreview: null,
        handledArtifacts: new Map(),
      };

      function settingsChromeMarkup() {
        return `
          <div id="${settingsModalId}" class="codex-usage-hud-settings-modal" hidden></div>
          ${composerBadgeEnabled
            ? `<div class="codex-usage-hud-token-breakdown" data-field="requestComposerBreakdown" role="tooltip" hidden></div>`
            : ""}
          <div class="codex-usage-hud-runtime-errors" data-field="runtimeErrorsPanel" hidden></div>
          ${restReminderToastMarkup()}
        `;
      }

      function loadPersistedSupportImages() {
        try {
          const raw = JSON.parse(ctx.storage.read(localStorage, supportImagesStorageKey, "[]"));
          if (!Array.isArray(raw)) return [];
          return raw.filter((item) => (
            item
            && typeof item === "object"
            && typeof item.src === "string"
            && item.src.startsWith("data:image/")
          ));
        } catch (_) {
          return [];
        }
      }

      function persistSupportImages(images) {
        try {
          const items = Array.isArray(images) ? images.filter(Boolean) : [];
          if (!items.length) return;
          ctx.storage.write(localStorage, supportImagesStorageKey, JSON.stringify(items));
        } catch (_) {}
      }

      function defaultHudSettings() {
        return {
          daily_budget_usd: 100,
          weekly_budget_usd: 400,
          daily_reset_time: "10:00",
          weekly_reset_weekday: 3,
          weekly_reset_time: "10:00",
          display_mode: "renderer",
          work_overlay_max_items: 6,
          pricing_url: "",
          budget_thresholds: [0.5, 0.8, 0.9, 1.0],
          weekly_adjustment_usd: 0,
          provider_settings: {},
          provider_scope_mode: "all",
          selected_providers: [],
          notification_only_providers: [],
          provider_registry: {},
          app_provider: "",
          support_url: "https://github.com/mingbingfeng/codex-usage-hud",
          rest_reminder_enabled: false,
          rest_reminder_interval_minutes: 45,
          rest_reminder_break_minutes: 2,
          rest_reminder_postpone_minutes: 10,
          rest_reminder_idle_reset_minutes: 0,
          rest_reminder_work_start_time: "09:00",
          rest_reminder_work_end_time: "18:00",
          rest_reminder_lunch_enabled: true,
          rest_reminder_lunch_start_time: "12:00",
          rest_reminder_lunch_end_time: "13:30",
          model_prices: {},
          pricing_versions: [],
          pricing_audit: [],
        };
      }

      function hudSettingsFromPayload() {
        const raw = currentPayload()?.settings || {};
        return { ...defaultHudSettings(), ...(raw && typeof raw === "object" ? raw : {}) };
      }

      function normalizePriceModel(value) {
        return String(value || "").trim().toLowerCase().replace(/-\\d{4}-\\d{2}-\\d{2}$/, "");
      }

      function priceModelPatternMatches(pattern, model) {
        const normalizedPattern = normalizePriceModel(pattern);
        const normalizedModel = normalizePriceModel(model);
        if (!normalizedPattern || !normalizedModel) return false;
        if (normalizedPattern.includes("*") || normalizedPattern.includes("?")) {
          const regexText = Array.from(normalizedPattern).map((char) => {
            if (char === "*") return ".*";
            if (char === "?") return ".";
            return char.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");
          }).join("");
          const regex = new RegExp(`^${regexText}$`);
          return regex.test(normalizedModel);
        }
        return normalizedModel === normalizedPattern || normalizedModel.startsWith(`${normalizedPattern}-`);
      }

      function configuredPriceModels(settings) {
        const prices = settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
        return Object.entries(prices).map(([key, price]) => String(price?.model || key || "").trim()).filter(Boolean);
      }

      function observedPriceModels() {
        const payload = currentPayload() || {};
        const values = [];
        if (payload.model) values.push(payload.model);
        if (Array.isArray(payload.observedModels)) values.push(...payload.observedModels);
        const seen = new Set();
        return values.map((item) => String(item || "").trim()).filter((item) => {
          if (!item || item === "n/a") return false;
          const key = normalizePriceModel(item);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      }

      function unknownPriceModels(settings) {
        const configured = configuredPriceModels(settings);
        return observedPriceModels().filter(
          (model) => !configured.some((pattern) => priceModelPatternMatches(pattern, model))
        );
      }

      function settingsBridgeUrl() {
        return String(currentPayload()?.settingsBridgeUrl || "").replace(/\/+$/, "");
      }

      function settingsPathLabel() {
        return String(currentPayload()?.settingsPath || "");
      }

      function appVersion() {
        return String(currentPayload()?.appVersion || "unknown");
      }

      function currentUpdateState() {
        const raw = currentPayload()?.updateState || {};
        return raw && typeof raw === "object" ? raw : {};
      }

      function workOverlaySelectableMax() {
        const value = Number(currentPayload()?.workOverlaySelectableMax ?? 6);
        return Number.isFinite(value) && value >= 1 ? Math.round(value) : 6;
      }

      function desktopOverlayDependency() {
        const raw = currentPayload()?.desktopOverlayDependency || {};
        return raw && typeof raw === "object" ? raw : {};
      }

      function desktopOverlayDependencyHtml() {
        const dependency = desktopOverlayDependency();
        const installed = !!dependency.installed;
        const installing = !!dependency.installing;
        const requiresRestart = !!dependency.requiresRestart;
        const canInstall = !!dependency.canInstall;
        const version = String(dependency.version || "").trim();
        if (installed) {
          return `
            <div class="codex-usage-hud-overlay-dependency" data-installed="true" title="保存后显示方形进度气泡；会话完成后收起为圆形总结。">
              <span class="codex-usage-hud-overlay-dependency-state">已安装</span>
              <span class="codex-usage-hud-overlay-dependency-version">${escapeHtml(version ? `PySide6 ${version}` : "PySide6 可用")}</span>
            </div>
          `;
        }
        const actions = [];
        if (canInstall && !installing) {
          actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-install-desktop-overlay">立即安装</button>');
        }
        if (!requiresRestart) {
          actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-enable-desktop-overlay">启用气泡</button>');
        } else {
          actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-restart">立即重启</button>');
        }
        const stateText = installing ? "正在安装" : (requiresRestart ? "需要重启" : "未安装");
        const noteText = installing
          ? "后台安装中"
          : (requiresRestart ? "重启后生效" : "需 PySide6");
        return `
          <div class="codex-usage-hud-overlay-dependency" data-installed="false" title="${escapeHtml(
            installing
              ? "气泡组件正在后台安装；完成后可启用。"
              : (requiresRestart
                ? "安装完成后重启 HUD，才能显示会话进度气泡。"
                : "会话进度气泡需要 PySide6 桌面组件。")
          )}">
            <span class="codex-usage-hud-overlay-dependency-state">${stateText}</span>
            <span class="codex-usage-hud-overlay-dependency-note">${escapeHtml(noteText)}</span>
            <div class="codex-usage-hud-overlay-dependency-actions">${actions.join("")}</div>
          </div>
        `;
      }

      function syncDesktopOverlayDependency() {
        const node = document.querySelector(`#${settingsModalId} [data-desktop-overlay-dependency="true"]`);
        if (node) node.innerHTML = desktopOverlayDependencyHtml();
      }

      function updateStateFromPayload(payload) {
        const raw = payload?.updateState || {};
        return raw && typeof raw === "object" ? raw : {};
      }

      function updateActionGlyph(state) {
        return String(state?.icon || "download") === "install" ? "⇪" : "↓";
      }

      function renderUpdateButtons(root, payload) {
        const state = updateStateFromPayload(payload);
        const visible = !!state?.visible;
        root.querySelectorAll('[data-action="update-action"]').forEach((node) => {
          if (!(node instanceof HTMLButtonElement)) return;
          node.hidden = !visible;
          if (!visible) {
            node.removeAttribute("title");
            node.removeAttribute("aria-label");
            node.dataset.state = "";
            node.dataset.icon = "";
            return;
          }
          const title = String(state?.title || state?.message || "发现新版本");
          node.textContent = updateActionGlyph(state);
          node.title = title;
          node.setAttribute("aria-label", title);
          node.dataset.state = String(state?.phase || "");
          node.dataset.icon = String(state?.icon || "download");
        });
      }

      function thresholdText(settings) {
        const items = Array.isArray(settings.budget_thresholds) ? settings.budget_thresholds : [];
        return items.map((value) => Number(value || 0)).filter((value) => value > 0).join(",");
      }

      function pricingLocalDateTimeValue(value = new Date()) {
        const date = value instanceof Date ? value : new Date(value);
        if (!Number.isFinite(date.getTime())) return "";
        const pad = (part) => String(part).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
      }

      function pricingEffectiveAtFromInput(input, { allowEmpty = false } = {}) {
        const text = String(input?.value || "").trim();
        if (!text && allowEmpty) return "";
        const parsed = new Date(text);
        const invalid = !text || !Number.isFinite(parsed.getTime());
        const future = !invalid && parsed.getTime() > Date.now();
        if (input) input.setAttribute("aria-invalid", String(invalid || future));
        if (invalid) throw new Error("请选择有效的新价格生效时间。");
        if (future) throw new Error("新价格生效时间不能晚于当前时间。");
        return parsed.toISOString();
      }

      function priceRowsHtml(settings) {
        const prices = settings.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
        const entries = Object.entries(prices);
        if (!entries.length) entries.push(["gpt-5.6-sol", { input: 5, output: 30, cached_input: 0.5, cache_write: 6.25 }]);
        return entries.map(([key, price]) => {
          const model = String(price?.model || key || "");
          const provider = String(price?.provider || "");
          const baseUrl = String(price?.base_url || price?.baseUrl || "");
          return `
          <div class="codex-usage-hud-price-row" data-price-row="true" data-price-key="${escapeHtml(key)}" data-price-model="${escapeHtml(model)}" data-price-provider="${escapeHtml(provider)}" data-price-base-url="${escapeHtml(baseUrl)}">
            <input data-price-field="model" value="${escapeHtml(model)}" aria-label="模型">
            <input data-price-field="input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.input ?? 0)}" aria-label="输入单价">
            <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cached_input ?? 0)}" aria-label="缓存读取单价">
            <input data-price-field="cache_write" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cache_write ?? 0)}" aria-label="缓存写入单价">
            <input data-price-field="output" type="number" min="0" step="0.000001" value="${escapeHtml(price?.output ?? 0)}" aria-label="输出单价">
            <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="${escapeHtml(provider)}" aria-label="渠道">
            <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="${escapeHtml(baseUrl)}" aria-label="Base URL">
          </div>
        `;
        }).join("");
      }

      function detectedPriceModelsHtml(settings) {
        const models = unknownPriceModels(settings);
        if (!models.length) return "";
        return `
          <div class="codex-usage-hud-price-detected">
            <span>检测到未计价模型</span>
            ${models.slice(0, 4).map((model) => `<button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-detected-model" data-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`).join("")}
          </div>
        `;
      }

      function settingsProviderNames(settings) {
        const registry = settings.provider_registry && typeof settings.provider_registry === "object" ? settings.provider_registry : {};
        const providerSettings = settings.provider_settings && typeof settings.provider_settings === "object" ? settings.provider_settings : {};
        const appProvider = String(settings.app_provider || "").trim().toLowerCase();
        const names = new Set([...Object.keys(registry), ...Object.keys(providerSettings)]);
        if (appProvider) names.add(appProvider);
        return Array.from(names).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean).sort();
      }

      function cloneSettingsPriceTable(value) {
        const prices = value && typeof value === "object" ? value : {};
        return Object.fromEntries(Object.entries(prices).map(([key, price]) => [
          key,
          price && typeof price === "object" ? { ...price } : {},
        ]));
      }

      function providerDraftFromSettings(settings, provider, enabled, notificationOnly) {
        const source = settings.provider_settings?.[provider] || {};
        const modelPrices = source.model_prices && typeof source.model_prices === "object"
          ? source.model_prices
          : settings.model_prices;
        return {
          enabled: !!enabled,
          notificationOnly: !!notificationOnly && !enabled,
          settings: {
            ...source,
            model_prices: cloneSettingsPriceTable(modelPrices),
            pricing_url: String(source.pricing_url ?? settings.pricing_url ?? ""),
            weekly_adjustment_usd: Number(source.weekly_adjustment_usd ?? settings.weekly_adjustment_usd ?? 0),
          },
        };
      }

      function ensureSettingsProviderDraft(settings, reset = false) {
        if (settingsProviderDraft && !reset) return settingsProviderDraft;
        const order = settingsProviderNames(settings);
        const appProvider = String(settings.app_provider || "").trim().toLowerCase();
        const selected = settings.provider_scope_mode === "custom"
          ? new Set((settings.selected_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean))
          : new Set(order);
        const notificationOnly = new Set(
          (settings.notification_only_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean)
        );
        if (appProvider) selected.add(appProvider);
        const requestedProvider = String(window[settingsProviderName] || "").trim().toLowerCase();
        const activeProvider = order.includes(requestedProvider)
          ? requestedProvider
          : (order.includes(appProvider) ? appProvider : (order[0] || ""));
        settingsProviderDraft = {
          activeProvider,
          appProvider,
          order,
          providers: Object.fromEntries(order.map((provider) => [
            provider,
            providerDraftFromSettings(
              settings,
              provider,
              selected.has(provider) || provider === appProvider,
              notificationOnly.has(provider) && !selected.has(provider) && provider !== appProvider,
            ),
          ])),
        };
        settingsDirtyProviders.clear();
        window[settingsProviderName] = activeProvider;
        return settingsProviderDraft;
      }

      function settingsProviderTabBadge(settings, provider) {
        const detail = settings.provider_registry?.[provider] || {};
        if (provider === settingsProviderDraft?.appProvider) return "App";
        if (detail.historicalOnly) return "历史";
        if (provider === "unknown") return "未知";
        return "";
      }

      function settingsProviderMeta(settings, provider) {
        const detail = settings.provider_registry?.[provider] || {};
        const profiles = Array.isArray(detail.profiles) ? detail.profiles.map((profile) => String(profile || "").trim()).filter(Boolean) : [];
        const parts = [];
        let tone = "";
        if (provider === settingsProviderDraft?.appProvider) {
          parts.push("Codex App · 必选");
          tone = "required";
        } else if (detail.historicalOnly) {
          parts.push("历史通道");
          tone = "historical";
        } else if (provider === "unknown") {
          parts.push("未知通道");
        }
        if (profiles.length) {
          parts.push(`${profiles.length > 1 ? "Profiles" : "Profile"}: ${profiles.join(", ")}`);
        }
        return { text: parts.join(" · "), tone };
      }

      function settingsProviderTabsHtml(settings) {
        const draft = ensureSettingsProviderDraft(settings);
        return draft.order.map((provider) => {
          const badge = settingsProviderTabBadge(settings, provider);
          const dirty = settingsDirtyProviders.has(provider);
          return `
            <button type="button" class="codex-usage-hud-provider-tab" role="tab"
              data-action="settings-provider-tab" data-provider-tab="true" data-provider="${escapeHtml(provider)}"
              aria-selected="${provider === draft.activeProvider}">
              <span>${escapeHtml(provider)}</span>
              ${badge ? `<span class="codex-usage-hud-provider-tab-badge">${escapeHtml(badge)}</span>` : ""}
              ${dirty ? '<span class="codex-usage-hud-provider-dirty-dot" aria-hidden="true"></span><span class="codex-usage-hud-settings-visually-hidden">有未保存修改</span>' : ""}
            </button>
          `;
        }).join("");
      }

      function settingsProviderEditorHtml(settings) {
        const draft = ensureSettingsProviderDraft(settings);
        const activeProvider = draft.activeProvider;
        const head = `
          <div class="codex-usage-hud-provider-editor-head">
            <div class="codex-usage-hud-price-title">模型单价</div>
            <div class="codex-usage-hud-provider-tabs" data-provider-tabs="true" role="tablist" aria-label="Provider">
              ${settingsProviderTabsHtml(settings)}
            </div>
            <div class="codex-usage-hud-price-unit">USD / 1M tokens</div>
          </div>
        `;
        const entry = draft.providers[activeProvider];
        if (!activeProvider || !entry) {
          return `${head}<div class="codex-usage-hud-provider-empty">尚未发现 Provider</div>`;
        }
        const providerSettings = entry.settings;
        const required = activeProvider === draft.appProvider;
        const meta = settingsProviderMeta(settings, activeProvider);
        const weeklyAdjustment = Number(providerSettings.weekly_adjustment_usd);
        const weeklyAdjustmentValue = Number.isFinite(weeklyAdjustment) && weeklyAdjustment > 0
          ? String(weeklyAdjustment)
          : "";
        const pricingUrlPlaceholder = "计费单价获取地址 · https://example.com/model-prices.json";
        return `
          ${head}
          <div class="codex-usage-hud-provider-context">
            <div class="codex-usage-hud-provider-scope-options">
              <label class="codex-usage-hud-provider-scope" ${required ? 'title="Codex App Provider 必须纳入统计"' : ""}>
                <input type="checkbox" data-provider-enabled="true" ${entry.enabled || required ? "checked" : ""} ${required ? "disabled" : ""}>
                <span>纳入统计</span>
              </label>
              <label class="codex-usage-hud-provider-scope" ${required ? 'title="Codex App Provider 必须纳入统计"' : ""}>
                <input type="checkbox" data-provider-notification-only="true" ${entry.notificationOnly && !required ? "checked" : ""} ${required ? "disabled" : ""}>
                <span>仅气泡通知不统计</span>
              </label>
              <div class="codex-usage-hud-provider-context-adjustment">
                <input data-setting-key="weekly_adjustment_usd" type="number" min="0" step="0.01" value="${escapeHtml(weeklyAdjustmentValue)}" placeholder="本周补充额度 USD" aria-label="本周补充额度 USD" title="本周补充额度 USD">
              </div>
            </div>
            <div class="codex-usage-hud-provider-meta" data-tone="${escapeHtml(meta.tone)}">${escapeHtml(meta.text)}</div>
          </div>
          <div class="codex-usage-hud-price-table">
            <div class="codex-usage-hud-price-header">
              <div>模型</div><div>输入</div><div>缓存读取</div><div>缓存写入</div><div>输出</div><div class="codex-usage-hud-price-advanced">渠道</div><div class="codex-usage-hud-price-advanced">Base URL</div>
            </div>
            <div data-price-rows="true">${priceRowsHtml(providerSettings)}</div>
            ${detectedPriceModelsHtml(providerSettings)}
            <div class="codex-usage-hud-price-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-model">添加模型</button>
              <input data-setting-key="pricing_url" value="${escapeHtml(providerSettings.pricing_url)}" placeholder="${escapeHtml(pricingUrlPlaceholder)}" aria-label="计费单价获取地址" title="${escapeHtml(pricingUrlPlaceholder)}">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-fetch-prices">拉取并预览</button>
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-pricing-icon-action" data-action="pricing-export" aria-label="导出价格 JSON" title="导出价格 JSON"><span aria-hidden="true">⇩</span></button>
              <button type="button" class="codex-usage-hud-settings-action codex-usage-hud-pricing-icon-action" data-action="pricing-import-open" aria-label="导入价格 JSON" title="导入价格 JSON"><span aria-hidden="true">⇧</span></button>
            </div>
          </div>
        `;
      }

      function revealSettingsProviderTab(tab) {
        const tabs = tab?.parentElement;
        if (!tab || !tabs) return;
        const left = tab.offsetLeft;
        const right = left + tab.offsetWidth;
        if (left < tabs.scrollLeft) {
          tabs.scrollLeft = left;
        } else if (right > tabs.scrollLeft + tabs.clientWidth) {
          tabs.scrollLeft = right - tabs.clientWidth;
        }
      }

      function renderSettingsProviderTabs() {
        const tabs = document.querySelector(`#${settingsModalId} [data-provider-tabs="true"]`);
        if (!tabs || !settingsProviderDraft) return;
        tabs.innerHTML = settingsProviderTabsHtml(hudSettingsFromPayload());
        revealSettingsProviderTab(tabs.querySelector('[aria-selected="true"]'));
      }

      function captureSettingsProviderForm() {
        const modal = document.getElementById(settingsModalId);
        const editor = modal?.querySelector('[data-provider-editor="true"]');
        const activeProvider = String(editor?.dataset.activeProvider || "").trim().toLowerCase();
        const entry = settingsProviderDraft?.providers?.[activeProvider];
        if (!editor || !activeProvider || !entry) return "";
        const modelPrices = {};
        editor.querySelectorAll("[data-price-row='true']").forEach((row) => {
          const model = String(row.querySelector("[data-price-field='model']")?.value || "").trim();
          if (!model) return;
          const provider = String(row.querySelector("[data-price-field='provider']")?.value || "").trim().toLowerCase();
          const baseUrl = String(row.querySelector("[data-price-field='base_url']")?.value || "").trim().replace(/\/+$/, "");
          const originalKey = String(row.dataset.priceKey || "").trim();
          const originalModel = String(row.dataset.priceModel || "").trim();
          const originalProvider = String(row.dataset.priceProvider || "").trim().toLowerCase();
          const originalBaseUrl = String(row.dataset.priceBaseUrl || "").trim().replace(/\/+$/, "");
          const field = (name) => {
            const value = Number(row.querySelector(`[data-price-field="${name}"]`)?.value);
            return Number.isFinite(value) && value >= 0 ? value : 0;
          };
          // Keep the persisted row key stable while editing numeric fields. The
          // provider/model form fields are optional scope overrides, so only
          // rebuild the key when one of those identity fields actually changes.
          const sameScope = originalKey
            && originalModel === model
            && originalProvider === provider
            && originalBaseUrl === baseUrl;
          const key = sameScope
            ? originalKey
            : (provider ? `${provider}/${model}` : (baseUrl ? `${baseUrl}/${model}` : model));
          const output = field("output");
          modelPrices[key] = {
            model,
            input: field("input"),
            cached_input: field("cached_input"),
            cache_write: field("cache_write"),
            output,
            reasoning: output,
          };
          if (provider) modelPrices[key].provider = provider;
          if (baseUrl) modelPrices[key].base_url = baseUrl;
        });
        const enabledNode = editor.querySelector('[data-provider-enabled="true"]');
        const notificationOnlyNode = editor.querySelector('[data-provider-notification-only="true"]');
        const pricingNode = editor.querySelector('[data-setting-key="pricing_url"]');
        const adjustmentNode = editor.querySelector('[data-setting-key="weekly_adjustment_usd"]');
        const adjustment = Number(adjustmentNode?.value);
        entry.enabled = activeProvider === settingsProviderDraft.appProvider || !!enabledNode?.checked;
        entry.notificationOnly = !entry.enabled && !!notificationOnlyNode?.checked;
        entry.settings = {
          ...entry.settings,
          model_prices: modelPrices,
          pricing_url: String(pricingNode?.value || "").trim(),
          weekly_adjustment_usd: Number.isFinite(adjustment) && adjustment >= 0 ? adjustment : 0,
        };
        return activeProvider;
      }

      function updateSettingsProviderDraftStatus() {
        const count = settingsDirtyProviders.size;
        if (count) setSettingsStatus(`${count} 个 Provider 有未保存修改`);
      }

      function markSettingsProviderDirty() {
        const activeProvider = captureSettingsProviderForm();
        if (!activeProvider) return;
        settingsDirtyProviders.add(activeProvider);
        renderSettingsProviderTabs();
        updateSettingsProviderDraftStatus();
      }

      function renderSettingsProviderEditor({ focusTab = false } = {}) {
        const editor = document.querySelector(`#${settingsModalId} [data-provider-editor="true"]`);
        if (!editor || !settingsProviderDraft) return;
        editor.dataset.activeProvider = settingsProviderDraft.activeProvider;
        editor.innerHTML = settingsProviderEditorHtml(hudSettingsFromPayload());
        const activeTab = editor.querySelector('[data-provider-tab="true"][aria-selected="true"]');
        revealSettingsProviderTab(activeTab);
        if (focusTab) activeTab?.focus?.();
        updateSettingsProviderDraftStatus();
      }

      function switchSettingsProvider(provider, { focusTab = false } = {}) {
        const nextProvider = String(provider || "").trim().toLowerCase();
        if (!settingsProviderDraft?.order.includes(nextProvider) || nextProvider === settingsProviderDraft.activeProvider) return;
        captureSettingsProviderForm();
        settingsProviderDraft.activeProvider = nextProvider;
        window[settingsProviderName] = nextProvider;
        renderSettingsProviderEditor({ focusTab });
      }

      function typedSettingsRequestId(prefix) {
        return `${String(prefix || "request")}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      }

      function renderSettingsModal(tab = "settings", status = "", { resetProviderDraft = false } = {}) {
        const root = document.getElementById(rootId);
        const modal = document.getElementById(settingsModalId);
        if (!root || !modal) return;
        const sessionCleanupConfirmToken = String(
          modal.querySelector('[data-session-cleanup-confirm="true"]')?.dataset.sessionCleanupConfirmToken || "",
        );
        if (!modal.hidden) {
          captureSettingsProviderForm();
          if (settingsActiveTab === "backgroundUsage") {
            captureBackgroundUsageScrollPositions();
          }
          if (settingsActiveTab === "storage") captureStorageUiState();
        }
        const settings = hudSettingsFromPayload();
        const activeTab = ["storage", "backgroundUsage", "support", "about"].includes(tab) ? tab : "settings";
        settingsActiveTab = activeTab;
        writeSettingsUiState(true, activeTab);
        if (activeTab === "settings") ensureSettingsProviderDraft(settings, resetProviderDraft);
        const path = settingsPathLabel();
        const bridge = settingsBridgeUrl();
        const defaultStatus = activeTab === "about"
          ? "可检查 GitHub Release 并启动 Windows 安装器。"
          : activeTab === "storage"
            ? "扫描和永久删除仅在用户明确操作时执行。"
            : activeTab === "backgroundUsage"
              ? "Tokens 来自本机日志；费用均为 HUD 估算。"
            : (bridge ? "设置将保存到本地配置文件" : "设置桥接未连接，可导出 JSON 手动写入配置文件");
        modal.innerHTML = `
          <div class="codex-usage-hud-settings-dialog" data-active-tab="${escapeHtml(activeTab)}" role="dialog" aria-modal="true" aria-label="codex-usage-hud 设置">
            <div class="codex-usage-hud-settings-head">
              <div class="codex-usage-hud-settings-title">codex-usage-hud v${escapeHtml(appVersion())}</div>
              <button type="button" class="codex-usage-hud-settings-close" data-action="settings-close" aria-label="关闭">×</button>
            </div>
            <div class="codex-usage-hud-settings-tabs" role="tablist">
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="settings" data-active="${activeTab === "settings"}">设置</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="storage" data-active="${activeTab === "storage"}">会话管理</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="backgroundUsage" data-active="${activeTab === "backgroundUsage"}">用量总览</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="support" data-active="${activeTab === "support"}">请作者喝咖啡</button>
              <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="about" data-active="${activeTab === "about"}">版本更新</button>
            </div>
            <div class="codex-usage-hud-settings-body">
              ${activeTab === "support" ? supportPanelHtml(settings, path) : activeTab === "about" ? aboutPanelHtml(path) : activeTab === "storage" ? storagePanelHtml() : activeTab === "backgroundUsage" ? backgroundUsagePanelHtml() : settingsPanelHtml(settings, bridge, path)}
            </div>
            <div class="codex-usage-hud-settings-actions">
              <div class="codex-usage-hud-settings-status" data-settings-status="true">${escapeHtml(status || defaultStatus)}</div>
              <div>
                ${activeTab === "settings" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-export">导出 JSON</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-restart" hidden>立即重启 HUD</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-save" data-primary="true">保存</button>' : activeTab === "backgroundUsage" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="background-usage-refresh">刷新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>' : activeTab === "about" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-check-update">检查更新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-install-update" data-primary="true">安装更新</button>' : '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>'}
              </div>
            </div>
          </div>
        `;
        modal.hidden = false;
        ensureRestReminderCountdownTicker();
        updateAboutActionButtons(currentUpdateState());
        if (activeTab === "storage") {
          restoreStorageUiState();
          restoreSessionCleanupConfirm(sessionCleanupConfirmToken);
        }
        if (activeTab === "backgroundUsage") {
          restoreBackgroundUsageScrollPositions();
          const revision = Math.max(0, Number(currentPayload()?.backgroundUsageRevision || 0));
          if (!backgroundUsageState.data || backgroundUsageState.loadedRevision !== revision) {
            void loadBackgroundUsage({ force: true });
          } else if (backgroundUsageState.selectedEventId && !backgroundUsageState.detail) {
            // Keep default markViewed=false here: explicit list click and the open
            // notification path pass markViewed:true themselves.
            void loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
          }
        }
      }

      function restoreOpenSettingsModal() {
        const modal = document.getElementById(settingsModalId);
        const settingsUiState = readSettingsUiState();
        if (!modal || !modal.hidden || settingsUiState?.open !== true) return false;
        renderSettingsModal(String(settingsUiState.tab || settingsActiveTab));
        return true;
      }

      function settingsPanelHtml(settings, bridge, path) {
        ensureSettingsProviderDraft(settings);
        const overlaySelectableMax = workOverlaySelectableMax();
        const overlayValue = Math.min(
          overlaySelectableMax,
          Math.max(0, Math.round(Number(settings.work_overlay_max_items) || 0)),
        );
        const overlayOptions = Array.from({ length: overlaySelectableMax + 1 }, (_, index) => `
          <option value="${index}" ${overlayValue === index ? "selected" : ""}>${index}${index === 0 ? " - 不启用" : ""}</option>
        `).join("");
        const weekdayOptions = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
          .map((label, index) => `<option value="${index}" ${Number(settings.weekly_reset_weekday) === index ? "selected" : ""}>${label}</option>`)
          .join("");
        return `
          <div class="codex-usage-hud-settings-grid">
            <div class="codex-usage-hud-settings-field">
              <label>日额度 USD</label>
              <input data-setting-key="daily_budget_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.daily_budget_usd)}">
            </div>
            <div class="codex-usage-hud-settings-field">
              <label>周额度 USD</label>
              <input data-setting-key="weekly_budget_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.weekly_budget_usd)}">
            </div>
            <div class="codex-usage-hud-settings-field">
              <label>日额度重置时间</label>
              <input data-setting-key="daily_reset_time" type="time" value="${escapeHtml(settings.daily_reset_time)}">
            </div>
            <div class="codex-usage-hud-settings-field">
              <label>周额度重置</label>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
                <select data-setting-key="weekly_reset_weekday">${weekdayOptions}</select>
                <input data-setting-key="weekly_reset_time" type="time" value="${escapeHtml(settings.weekly_reset_time)}">
              </div>
            </div>
            <div class="codex-usage-hud-settings-compact-row">
              <div class="codex-usage-hud-settings-field">
                <label>超额提醒阈值</label>
                <input data-setting-key="budget_thresholds" value="${escapeHtml(thresholdText(settings))}" placeholder="50,80,100">
              </div>
              <div class="codex-usage-hud-settings-field">
                <label>会话进度气泡数量（0 为关闭）</label>
                <select data-setting-key="work_overlay_max_items">${overlayOptions}</select>
              </div>
              <div class="codex-usage-hud-settings-field">
                <label>气泡运行环境</label>
                <div data-desktop-overlay-dependency="true">${desktopOverlayDependencyHtml()}</div>
              </div>
            </div>
            <div class="codex-usage-hud-provider-editor" data-provider-editor="true" data-active-provider="${escapeHtml(settingsProviderDraft?.activeProvider || "")}">
              ${settingsProviderEditorHtml(settings)}
            </div>
            <div class="codex-usage-hud-settings-footnote">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit" data-variant="ghost">退出 HUD</button>
              <div class="codex-usage-hud-settings-status">配置文件：${escapeHtml(path || "未提供")} ${bridge ? "" : "（桥接未连接）"}</div>
            </div>
          </div>
        `;
      }

      function restReminderStatusTitle(state, configEnabled) {
        if (!configEnabled) return "未开启";
        switch (String(state || "")) {
          case "work": return "专注中";
          case "prompt": return "等待选择";
          case "postponed": return "已延迟";
          case "resting":
          case "break": return "休息中";
          case "lunch": return "午休";
          case "away": return "离开中";
          case "off": return "非工作时段";
          case "disabled": return "未开启";
          default: return "专注中";
        }
      }

"""

_TEXT_SUFFIX = r"""      function setSettingsStatus(text, kind = "") {
        const node = document.querySelector(`#${settingsModalId} [data-settings-status="true"]`);
        if (!node) return;
        node.textContent = String(text || "");
        node.dataset.kind = kind;
      }

      function setSettingsRestartVisible(visible) {
        const node = document.querySelector(`#${settingsModalId} [data-action="settings-restart"]`);
        if (node) node.hidden = !visible;
      }

      function setSettingsActionState(actionName, { label = "", disabled = false } = {}) {
        const node = document.querySelector(`#${settingsModalId} [data-action="${actionName}"]`);
        if (!(node instanceof HTMLButtonElement)) return;
        if (label) node.textContent = label;
        node.disabled = !!disabled;
      }

      function formatRestReminderClock(milliseconds) {
        if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "--:--:--";
        return new Date(milliseconds).toLocaleTimeString("zh-CN", {
          hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
        });
      }

      function formatRestReminderInputTime(milliseconds) {
        const value = Number.isFinite(milliseconds) && milliseconds > 0
          ? new Date(milliseconds)
          : new Date();
        return [value.getHours(), value.getMinutes()]
          .map((item) => String(item).padStart(2, "0"))
          .join(":");
      }

      function formatRestReminderRemaining(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) return "--:--:--";
        const total = Math.max(0, Math.ceil(seconds));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const remainder = total % 60;
        return [hours, minutes, remainder].map((value) => String(value).padStart(2, "0")).join(":");
      }

      function syncRestReminderCountdown() {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "support") return;
        const timing = currentPayload()?.restReminder;
        const configEnabled = !!timing?.enabled;
        const running = configEnabled && timing?.running !== false;
        const state = String(timing?.state || (configEnabled ? "work" : "disabled"));
        const statusBox = modal.querySelector('.codex-usage-hud-rest-reminder-status');
        const statusTitle = modal.querySelector('[data-rest-reminder-status-title="true"]');
        const remaining = modal.querySelector('[data-rest-reminder-remaining="true"]');
        const startInput = modal.querySelector('[data-rest-reminder-start-time="true"]');
        if (statusBox) statusBox.dataset.state = state;
        if (statusTitle) statusTitle.textContent = restReminderStatusTitle(state, configEnabled);
        if (startInput && startInput.dataset.userEdited !== "true") {
          const nextStart = formatRestReminderInputTime(Number(timing?.timerStartedAtMs));
          if (startInput.value !== nextStart) startInput.value = nextStart;
        }
        if (remaining) {
          if (state === "resting" || state === "break") {
            remaining.textContent = formatRestReminderRemaining(
              (Number(timing?.restEndsAtMs || timing?.breakEndsAtMs) - Date.now()) / 1000,
            );
          } else if (state === "prompt") {
            remaining.textContent = formatRestReminderRemaining(
              (Number(timing?.promptEndsAtMs) - Date.now()) / 1000,
            );
          } else if (state === "postponed") {
            remaining.textContent = formatRestReminderRemaining(
              (Number(timing?.postponeEndsAtMs) - Date.now()) / 1000,
            );
          } else {
            remaining.textContent = running
              ? formatRestReminderRemaining((Number(timing?.nextReminderAtMs) - Date.now()) / 1000)
              : "--:--:--";
          }
        }
      }

      function ensureRestReminderCountdownTicker() {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "support") {
          if (restReminderCountdownTimer) {
            ctx.lifecycle.clearInterval(restReminderCountdownTimer);
            restReminderCountdownTimer = 0;
          }
          return;
        }
        syncRestReminderCountdown();
        if (!restReminderCountdownTimer) {
          restReminderCountdownTimer = ctx.lifecycle.interval(
            "rest_reminder_settings",
            syncRestReminderCountdown,
            1000,
          );
        }
      }

      function updateAboutActionButtons(state) {
        const phase = String(state?.phase || "");
        const progressText = String(state?.progressText || "").trim();
        let checkLabel = "检查更新";
        let installLabel = "安装更新";
        let disableCheck = false;
        let disableInstall = false;
        if (phase === "checking") {
          checkLabel = "检查中...";
          installLabel = "请稍候";
          disableCheck = true;
          disableInstall = true;
        } else if (phase === "downloading") {
          installLabel = progressText ? `下载中 ${progressText}` : "下载中...";
          disableCheck = true;
          disableInstall = true;
        } else if (phase === "ready") {
          installLabel = "打开安装器";
        }
        setSettingsActionState("settings-check-update", {
          label: checkLabel,
          disabled: disableCheck,
        });
        setSettingsActionState("settings-install-update", {
          label: installLabel,
          disabled: disableInstall,
        });
      }

      function showSettingsRestartPrompt(message, kind = "error") {
        setSettingsStatus(`${message} 是否立即重启 HUD？`, kind);
        setSettingsRestartVisible(true);
      }

      function setSettingsLoadingText({ kicker = "", title = "", body = "" } = {}) {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"][data-loading-mode]`);
        if (!layer) return;
        const kickerNode = layer.querySelector(".codex-usage-hud-settings-confirm-kicker");
        const titleNode = layer.querySelector(".codex-usage-hud-settings-confirm-title");
        const bodyNode = layer.querySelector(".codex-usage-hud-settings-confirm-body");
        if (kickerNode) kickerNode.textContent = String(kicker || "");
        if (titleNode) titleNode.textContent = String(title || "");
        if (bodyNode) bodyNode.textContent = String(body || "");
      }

      function syncSettingsUpdateLoading(payload) {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"][data-loading-mode]`);
        if (!layer) return;
        const mode = String(layer.dataset.loadingMode || "");
        const state = updateStateFromPayload(payload);
        const phase = String(state?.phase || "");
        const progressText = String(state?.progressText || "").trim();
        if (mode === "check-update") {
          if (phase === "checking") {
            setSettingsLoadingText({
              kicker: "正在检查",
              title: "正在检查更新",
              body: "HUD daemon 正在查询 GitHub Release。通常只需 1 到 3 秒。",
            });
            return;
          }
          closeSettingsConfirm();
          return;
        }
        if (mode === "install-update") {
          if (phase === "checking") {
            setSettingsLoadingText({
              kicker: "正在准备",
              title: "正在检查并准备安装更新",
              body: "HUD daemon 正在查询 GitHub Release，并准备下载安装包。",
            });
            return;
          }
          if (phase === "downloading") {
            setSettingsLoadingText({
              kicker: "正在下载",
              title: "正在下载安装更新",
              body: progressText
                ? `当前进度：${progressText}\n\n下载完成后会自动启动安装器。`
                : "正在下载 Windows 安装包。\n\n下载完成后会自动启动安装器。",
            });
            return;
          }
          closeSettingsConfirm();
        }
      }

      function pricingArtifactSeen(status, suffix) {
        const requestId = String(status?.requestId || "");
        const action = String(status?.action || "pricing");
        const key = `${requestId || action}:${suffix}`;
        if (pricingWorkflowState.handledArtifacts.has(key)) return true;
        pricingWorkflowState.handledArtifacts.set(key, true);
        while (pricingWorkflowState.handledArtifacts.size > 64) {
          const oldest = pricingWorkflowState.handledArtifacts.keys().next().value;
          pricingWorkflowState.handledArtifacts.delete(oldest);
        }
        return false;
      }

      function applyPricingCommandStatus(status) {
        if (!status || typeof status !== "object") return;
        const action = String(status.action || "");
        if (String(status.kind || "") === "error") return;
        if (
          status.pricingImpactPreview
          && action === "pricingImpactPreview"
          && (!pricingWorkflowState.impactRequestId
            || !status.requestId
            || String(status.requestId) === pricingWorkflowState.impactRequestId)
        ) {
          pricingWorkflowState.impactPreview = status.pricingImpactPreview;
          pricingWorkflowState.impactEffectiveAt = String(
            status.pricingImpactPreview.effectiveAt
            || status.pricingImpactEffectiveAt
            || pricingWorkflowState.impactEffectiveAt
            || "",
          );
          renderPricingImpactPreview(status.pricingImpactPreview);
        }
        if (
          status.pricingPreview
          && ["pricingImportPreview", "fetchPricesPreview"].includes(action)
          && !pricingArtifactSeen(status, "preview")
        ) {
          openPricingImportPreview(status.pricingPreview, status.pricingPayload);
        }
        if (
          status.pricingPath
          && action === "pricingExport"
          && !pricingArtifactSeen(status, "export")
        ) {
          openPricingExportPrompt(status);
        }
        if (
          status.pricingRecalculationPreview
          && action === "pricingRecalculationPreview"
          && !pricingArtifactSeen(status, "recalculation-preview")
        ) {
          openPricingRecalculationPreview(status.pricingRecalculationPreview);
        }
        if (action === "savePricing") {
          settingsDirtyProviders.clear();
          renderSettingsProviderTabs();
        }
      }

      function applySettingsCommandStatus(payload) {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden) return;
        const state = updateStateFromPayload(payload);
        updateAboutActionButtons(state);
        syncDesktopOverlayDependency();
        syncSettingsUpdateLoading(payload);
        const status = payload?.settingsCommandStatus;
        if (status && typeof status === "object" && String(status.message || "")) {
          setSettingsStatus(status.message || "", status.kind || "");
          setSettingsRestartVisible(!!status.restartVisible);
          applyPricingCommandStatus(status);
          const restSaveRequestId = String(status.restReminderSaveRequestId || "");
          if (
            status.restReminderSaved === true
            && !String(status.kind || "")
            && restSaveRequestId
            && restSaveRequestId !== restReminderSavedRequestId
          ) {
            const startNode = modal.querySelector('[data-rest-reminder-start-time="true"]');
            if (startNode) delete startNode.dataset.userEdited;
            restReminderSavedRequestId = restSaveRequestId;
          }
          return;
        }
        setSettingsStatus(state.message || state.title || "", state.error ? "error" : "");
        setSettingsRestartVisible(false);
      }

      function submitSettingsCommand(command, pendingMessage, { preserveOverlay = false } = {}) {
        const payload = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          createdAt: Date.now(),
          ...command,
        };
        const bridge = settingsBridgeUrl();
        if (!bridge) {
          setSettingsStatus("无法提交设置命令：settings bridge 未连接", "error");
          return false;
        }
        try {
          if (ctx.bindings.available(settingsCommandBindingName)) {
            try {
              ctx.bindings.send(settingsCommandBindingName, payload);
            } catch (error) {
              setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
              return false;
            }
          } else {
          fetch(`${bridge}/command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            keepalive: true,
          }).catch((error) => {
            setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
          });
          }
        } catch (error) {
          setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
          return false;
        }
        setSettingsStatus(pendingMessage || "设置命令已提交，等待 HUD daemon 写入本地配置...");
        setSettingsRestartVisible(false);
        if (!preserveOverlay) closeSettingsConfirm();
        return true;
      }

      function settingsDialogRoot() {
        return document.querySelector(`#${settingsModalId} .codex-usage-hud-settings-dialog`);
      }

      function closeSettingsConfirm() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (layer) layer.remove();
      }

      function openSettingsLoading({ kicker = "正在处理", title = "", body = "", mode = "" } = {}) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        if (mode) layer.dataset.loadingMode = mode;
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="status" aria-live="polite" aria-label="${escapeHtml(title || "正在处理设置变更")}">
            <div class="codex-usage-hud-settings-confirm-kicker">${escapeHtml(kicker)}</div>
            <div class="codex-usage-hud-settings-confirm-title">${escapeHtml(title)}</div>
            <div class="codex-usage-hud-settings-confirm-body">${escapeHtml(body)}</div>
            <div class="codex-usage-hud-settings-loading-track" aria-hidden="true">
              <div class="codex-usage-hud-settings-loading-bar"></div>
              <div class="codex-usage-hud-settings-loading-glow"></div>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function collectSettingsForm() {
        const modal = document.getElementById(settingsModalId);
        const settings = hudSettingsFromPayload();
        const settingNode = (key) => modal?.querySelector(`[data-setting-key="${key}"]`);
        const read = (key) => settingNode(key)?.value;
        const numberValue = (key, fallback) => {
          const value = Number(read(key));
          return Number.isFinite(value) && value >= 0 ? value : fallback;
        };
        const integerValue = (key, fallback, min, max) => {
          const value = Number(read(key));
          if (!Number.isFinite(value)) return fallback;
          return Math.min(max, Math.max(min, Math.round(value)));
        };
        captureSettingsProviderForm();
        const draft = ensureSettingsProviderDraft(settings);
        const providerSettings = { ...(settings.provider_settings || {}) };
        draft.order.forEach((provider) => {
          providerSettings[provider] = {
            ...(providerSettings[provider] || {}),
            ...(draft.providers[provider]?.settings || {}),
          };
        });
        const selectedProviders = draft.order.filter((provider) => (
          provider === draft.appProvider || !!draft.providers[provider]?.enabled
        ));
        const notificationOnlyProviders = draft.order.filter((provider) => (
          provider !== draft.appProvider
          && !draft.providers[provider]?.enabled
          && !!draft.providers[provider]?.notificationOnly
        ));
        const allProvidersSelected = draft.order.every((provider) => selectedProviders.includes(provider));
        const displayMode = "renderer";
        const next = {
          ...settings,
          daily_budget_usd: numberValue("daily_budget_usd", settings.daily_budget_usd),
          weekly_budget_usd: numberValue("weekly_budget_usd", settings.weekly_budget_usd),
          daily_reset_time: String(read("daily_reset_time") || settings.daily_reset_time),
          weekly_reset_weekday: Number(read("weekly_reset_weekday") ?? settings.weekly_reset_weekday),
          weekly_reset_time: String(read("weekly_reset_time") || settings.weekly_reset_time),
          display_mode: displayMode,
          work_overlay_max_items: integerValue(
            "work_overlay_max_items",
            Number(settings.work_overlay_max_items) || 0,
            0,
            workOverlaySelectableMax(),
          ),
          pricing_url: String(settings.pricing_url || "").trim(),
          provider_settings: providerSettings,
          provider_scope_mode: allProvidersSelected ? "all" : "custom",
          selected_providers: selectedProviders,
          notification_only_providers: notificationOnlyProviders,
          budget_thresholds: String(read("budget_thresholds") || "")
            .split(",")
            .map((item) => Number(item.trim()))
            .filter((item) => Number.isFinite(item) && item > 0),
          weekly_adjustment_usd: settings.weekly_adjustment_usd,
          support_url: String(settings.support_url || "https://github.com/mingbingfeng/codex-usage-hud").trim(),
          model_prices: settings.model_prices,
        };
        // Rest-reminder controls live on the support tab; keep previous values when absent.
        if (settingNode("rest_reminder_enabled")) {
          next.rest_reminder_enabled = !!settingNode("rest_reminder_enabled").checked;
        }
        if (settingNode("rest_reminder_interval_minutes")) {
          next.rest_reminder_interval_minutes = integerValue(
            "rest_reminder_interval_minutes",
            Number(settings.rest_reminder_interval_minutes) || 45,
            1,
            180,
          );
        }
        if (settingNode("rest_reminder_break_minutes")) {
          next.rest_reminder_break_minutes = integerValue(
            "rest_reminder_break_minutes",
            Number(settings.rest_reminder_break_minutes) || 2,
            1,
            10,
          );
        }
        if (settingNode("rest_reminder_postpone_minutes")) {
          next.rest_reminder_postpone_minutes = integerValue(
            "rest_reminder_postpone_minutes",
            Number(settings.rest_reminder_postpone_minutes) || 10,
            5,
            30,
          );
        }
        [
          "rest_reminder_work_start_time",
          "rest_reminder_work_end_time",
          "rest_reminder_lunch_start_time",
          "rest_reminder_lunch_end_time",
        ].forEach((key) => {
          if (settingNode(key)) next[key] = String(read(key) || settings[key] || "");
        });
        if (settingNode("rest_reminder_lunch_enabled")) {
          next.rest_reminder_lunch_enabled = !!settingNode("rest_reminder_lunch_enabled").checked;
        }
        const startNode = modal?.querySelector('[data-rest-reminder-start-time="true"]');
        if (startNode?.value && startNode.dataset.userEdited === "true") {
          const [hour, minute] = String(startNode.value).split(":").map(Number);
          if (Number.isFinite(hour) && Number.isFinite(minute)) {
            const started = new Date();
            started.setHours(hour, minute, 0, 0);
            next.rest_reminder_timer_started_at_ms = started.getTime();
          }
        }
        return next;
      }

      function pricingTableFingerprint(settings) {
        const providers = settings?.provider_settings && typeof settings.provider_settings === "object"
          ? settings.provider_settings
          : {};
        const normalizedProviders = Object.fromEntries(Object.entries(providers).sort(([left], [right]) => left.localeCompare(right)).map(([provider, value]) => [
          provider,
          value?.model_prices && typeof value.model_prices === "object" ? value.model_prices : {},
        ]));
        return JSON.stringify({
          model_prices: settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {},
          provider_settings: normalizedProviders,
        });
      }

      function pricingChanged(settings) {
        return pricingTableFingerprint(settings) !== pricingTableFingerprint(hudSettingsFromPayload());
      }

      function pricingTablesByScope(settings) {
        const tables = new Map();
        tables.set("", settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {});
        const providers = settings?.provider_settings && typeof settings.provider_settings === "object"
          ? settings.provider_settings
          : {};
        Object.entries(providers).forEach(([provider, value]) => {
          tables.set(
            String(provider || "").trim().toLowerCase(),
            value?.model_prices && typeof value.model_prices === "object" ? value.model_prices : {},
          );
        });
        return tables;
      }

      function pricingRowFingerprint(row, fallbackModel = "") {
        const number = (value) => {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : 0;
        };
        return JSON.stringify({
          model: String(row?.model || fallbackModel || "").trim(),
          provider: String(row?.provider || "").trim().toLowerCase(),
          baseUrl: String(row?.base_url || row?.baseUrl || "").trim(),
          input: number(row?.input),
          cachedInput: number(row?.cached_input),
          cacheWrite: number(row?.cache_write),
          output: number(row?.output),
          reasoning: number(row?.reasoning ?? row?.output),
        });
      }

      function pricingShortPrice(row) {
        if (!row || typeof row !== "object") return "未设置";
        const number = (value) => {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : 0;
        };
        return `输入 ${number(row.input)} / 输出 ${number(row.output)}`;
      }

      function pricingImpactBucket(preview, period) {
        const direct = preview?.[period];
        if (direct && typeof direct === "object") return direct;
        const components = preview?.components && typeof preview.components === "object"
          ? preview.components
          : {};
        const sessions = components.sessions && typeof components.sessions === "object"
          ? components.sessions
          : {};
        const background = components.background && typeof components.background === "object"
          ? components.background
          : {};
        const left = sessions[period] && typeof sessions[period] === "object" ? sessions[period] : {};
        const right = background[period] && typeof background[period] === "object" ? background[period] : {};
        return {
          recordCount: Number(left.recordCount || 0) + Number(right.recordCount || 0),
          pricedCount: Number(left.pricedCount || 0) + Number(right.pricedCount || 0),
          unavailableCount: Number(left.unavailableCount || 0) + Number(right.unavailableCount || 0),
          costUsd: Number(left.costUsd || 0) + Number(right.costUsd || 0),
        };
      }

      function pricingImpactMoney(value) {
        const number = Number(value);
        return Number.isFinite(number) ? `$${number.toFixed(6)}` : "暂无";
      }

      function pricingImpactPreviewHtml(preview) {
        if (!preview || typeof preview !== "object") {
          return '<div class="codex-usage-hud-pricing-impact">正在估算已知历史用量...</div>';
        }
        const before = pricingImpactBucket(preview, "before");
        const after = pricingImpactBucket(preview, "after");
        const component = (label, value) => {
          const bucket = value && typeof value === "object" ? value : {};
          return `<span>${escapeHtml(label)}<strong>${Number(bucket.recordCount || 0)} 条 · ${pricingImpactMoney(bucket.costUsd)}</strong></span>`;
        };
        return `
          <div class="codex-usage-hud-pricing-impact-title">已知用量影响预览</div>
          <div class="codex-usage-hud-pricing-preview-grid">
            ${component("生效前", before)}
            ${component("生效后", after)}
          </div>
          <div class="codex-usage-hud-pricing-impact-components">
            ${component("普通会话生效前", preview?.ordinary?.before || preview?.sessions?.before)}
            ${component("后台请求生效前", preview?.background?.before)}
            ${component("普通会话生效后", preview?.ordinary?.after || preview?.sessions?.after)}
            ${component("后台请求生效后", preview?.background?.after)}
          </div>
        `;
      }

      function renderPricingImpactPreview(preview = pricingWorkflowState.impactPreview) {
        const node = document.querySelector(`#${settingsModalId} [data-pricing-impact-preview="true"]`);
        if (!node) return;
        node.innerHTML = pricingImpactPreviewHtml(preview);
        const confirm = document.querySelector(`#${settingsModalId} [data-action="pricing-effective-confirm"], #${settingsModalId} [data-action="pricing-import-commit"]`);
        if (confirm) {
          const mode = String(pricingWorkflowState.pendingMode || "");
          confirm.disabled = mode !== "fetch" && !pricingWorkflowState.impactPreview;
        }
      }

      function pricingImportPayloadForEffectiveAt(effectiveAt) {
        const source = pricingWorkflowState.importSourcePayload || pricingWorkflowState.importPayload;
        if (!source || typeof source !== "object") return source;
        if (!Array.isArray(source.prices)) return source;
        return {
          ...source,
          prices: source.prices.map((row) => (
            row && typeof row === "object" && !String(row.effective_at || row.effectiveAt || "").trim()
              ? { ...row, effective_at: effectiveAt }
              : row
          )),
        };
      }

      function refreshPricingImpactPreview({ mode = "save", input = null } = {}) {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (!layer || !pricingWorkflowState.pendingSettings && mode === "save" && !pricingWorkflowState.importPayload) return false;
        let effectiveAt;
        try {
          effectiveAt = pricingEffectiveAtFromInput(
            input || layer.querySelector('[data-pricing-effective-at="true"]') || layer.querySelector('[data-pricing-import-effective-at="true"]'),
          );
        } catch (error) {
          pricingWorkflowState.impactPreview = null;
          renderPricingImpactPreview(null);
          setSettingsStatus(error?.message || String(error), "error");
          return false;
        }
        pricingWorkflowState.impactPreview = null;
        pricingWorkflowState.impactEffectiveAt = effectiveAt;
        renderPricingImpactPreview(null);
        const command = {
          action: "pricingImpactPreview",
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          effectiveAt,
          ...(mode === "import"
            ? { payload: pricingImportPayloadForEffectiveAt(effectiveAt) }
            : { settings: pricingWorkflowState.pendingSettings || collectSettingsForm() }),
        };
        const submitted = submitSettingsCommand(
          command,
          "正在估算价格变更对已知用量的影响...",
          { preserveOverlay: true },
        );
        if (submitted) pricingWorkflowState.impactRequestId = String(command.id || "");
        return submitted;
      }

      function pricingChangeSummaryHtml(previous, candidate) {
        const beforeTables = pricingTablesByScope(previous);
        const afterTables = pricingTablesByScope(candidate);
        const scopes = Array.from(new Set([...beforeTables.keys(), ...afterTables.keys()])).sort();
        const changes = [];
        scopes.forEach((scope) => {
          const before = beforeTables.get(scope) || {};
          const after = afterTables.get(scope) || {};
          const models = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
          models.forEach((model) => {
            const oldRow = before[model];
            const newRow = after[model];
            if (pricingRowFingerprint(oldRow, model) === pricingRowFingerprint(newRow, model)) return;
            changes.push({
              provider: scope || "全局",
              model: String(newRow?.model || oldRow?.model || model || "未命名模型"),
              oldRow,
              newRow,
            });
          });
        });
        if (!changes.length) return "";
        const rows = changes.slice(0, 6).map((change) => `
          <div><strong>${escapeHtml(change.provider)}</strong> · ${escapeHtml(change.model)}<br><span>${escapeHtml(pricingShortPrice(change.oldRow))} -> ${escapeHtml(pricingShortPrice(change.newRow))}</span></div>
        `).join("");
        const more = changes.length > 6 ? `<div>另有 ${changes.length - 6} 个模型价格变更</div>` : "";
        return `<div class="codex-usage-hud-pricing-preview-list" aria-label="价格变更摘要">${rows}${more}</div>`;
      }

      function openPricingEffectiveDialog({ mode, settings = null, provider = "", url = "" }) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.pendingSettings = settings;
        pricingWorkflowState.pendingMode = String(mode || "save");
        pricingWorkflowState.pendingProvider = String(provider || "").trim().toLowerCase();
        pricingWorkflowState.pendingUrl = String(url || "").trim();
        pricingWorkflowState.impactPreview = null;
        pricingWorkflowState.impactEffectiveAt = "";
        pricingWorkflowState.impactRequestId = "";
        const changeSummary = mode === "save"
          ? pricingChangeSummaryHtml(hudSettingsFromPayload(), settings)
          : "";
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="设置新价格的生效时间">
            <div class="codex-usage-hud-settings-confirm-kicker">价格版本</div>
            <div class="codex-usage-hud-settings-confirm-title">设置新价格的生效时间</div>
            <div class="codex-usage-hud-settings-confirm-body">发生在此时间之前的用量继续按旧价格计算；此时间及之后的用量按新价格计算。</div>
            <label class="codex-usage-hud-pricing-field">新价格生效时间
              <input type="datetime-local" data-pricing-effective-at="true" value="${escapeHtml(pricingLocalDateTimeValue())}" max="${escapeHtml(pricingLocalDateTimeValue())}">
            </label>
            ${changeSummary}
            <div class="codex-usage-hud-pricing-impact" data-pricing-impact-preview="true">${mode === "save" ? "正在估算已知历史用量..." : "拉取结果会先进入导入预览，不会立即写入。"}</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-effective-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-effective-confirm" data-primary="true">${mode === "save" ? "确认并保存" : "拉取并预览"}</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        const effectiveInput = layer.querySelector('[data-pricing-effective-at="true"]');
        if (effectiveInput && mode === "save") {
          let refreshTimer = 0;
          const refresh = () => {
            if (refreshTimer) clearTimeout(refreshTimer);
            refreshTimer = window.setTimeout(() => {
              refreshPricingImpactPreview({ mode: "save", input: effectiveInput });
            }, 120);
          };
          effectiveInput.addEventListener("input", refresh);
          effectiveInput.addEventListener("change", refresh);
          refreshPricingImpactPreview({ mode: "save", input: effectiveInput });
        }
        layer.querySelector('[data-pricing-effective-at="true"]')?.focus?.();
      }

      function confirmPricingEffectiveAt() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        const input = layer?.querySelector('[data-pricing-effective-at="true"]');
        try {
          const effectiveAt = pricingEffectiveAtFromInput(input);
          const mode = String(pricingWorkflowState.pendingMode || "save");
          if (
            mode === "save"
            && (!pricingWorkflowState.impactPreview
              || pricingWorkflowState.impactEffectiveAt !== effectiveAt)
          ) {
            refreshPricingImpactPreview({ mode: "save", input });
            setSettingsStatus("正在刷新价格影响预览，请稍后确认。", "info");
            return;
          }
          if (mode === "fetch") {
            submitSettingsCommand({
              action: "fetchPricesPreview",
              provider: pricingWorkflowState.pendingProvider,
              url: pricingWorkflowState.pendingUrl,
              defaultEffectiveAt: effectiveAt,
            }, "正在拉取并校验价格 JSON...");
            return;
          }
          submitSettingsCommand({
            action: "savePricing",
            settings: pricingWorkflowState.pendingSettings || collectSettingsForm(),
            effectiveAt,
          }, "正在保存新的价格版本...");
        } catch (error) {
          setSettingsStatus(error?.message || String(error), "error");
        }
      }

      function openPricingExportPrompt(status) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        const path = String(status?.pricingPath || "").trim();
        const filename = String(
          status?.filename || path.split(/[\\/]/).pop() || "codex-usage-hud-pricing.json",
        ).trim();
        if (!path || !filename) return;
        closeSettingsConfirm();
        const usedTemplate = status?.pricingUsedTemplate === true;
        const body = usedTemplate
          ? `当前模型价格表为空，已使用 gpt-5.6-sol 内置价格作为模板。\n\n文件已生成到：\n${path}\n\n是否打开这个文件？`
          : `已按当前用户模型单价配置生成。\n\n文件已生成到：\n${path}\n\n是否打开这个文件？`;
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.pricingExportPrompt = "true";
        layer.dataset.pricingFilename = filename;
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="价格 JSON 已生成">
            <div class="codex-usage-hud-settings-confirm-kicker">价格 JSON</div>
            <div class="codex-usage-hud-settings-confirm-title">价格文件已生成</div>
            <div class="codex-usage-hud-settings-confirm-body">${escapeHtml(body)}</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-open-cancel" data-variant="ghost">暂不打开</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-open" data-primary="true">打开文件</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="pricing-open"]')?.focus?.();
      }

      function openPricingImportDialog() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.importPayload = null;
        pricingWorkflowState.importSourcePayload = null;
        pricingWorkflowState.importPreview = null;
        pricingWorkflowState.impactPreview = null;
        pricingWorkflowState.impactEffectiveAt = "";
        pricingWorkflowState.impactRequestId = "";
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-pricing-dialog" role="dialog" aria-modal="true" aria-label="导入价格 JSON">
            <div class="codex-usage-hud-settings-confirm-kicker">价格 JSON</div>
            <div class="codex-usage-hud-settings-confirm-title">导入并预览</div>
            <label class="codex-usage-hud-pricing-field">选择 JSON 文件
              <input type="file" accept="application/json,.json" data-pricing-import-file="true">
            </label>
            <label class="codex-usage-hud-pricing-field">或粘贴 JSON
              <textarea data-pricing-import-text="true" spellcheck="false" placeholder="{ &quot;schema_version&quot;: 1, &quot;unit&quot;: &quot;USD_per_1M_tokens&quot;, &quot;prices&quot;: [] }"></textarea>
            </label>
            <label class="codex-usage-hud-pricing-field">缺省的新价格生效时间
              <input type="datetime-local" data-pricing-import-effective-at="true" value="${escapeHtml(pricingLocalDateTimeValue())}" max="${escapeHtml(pricingLocalDateTimeValue())}">
            </label>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-preview" data-primary="true">校验并预览</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      async function readPricingImportFile(input) {
        const file = input?.files?.[0];
        if (!file) return;
        try {
          if (Number(file.size || 0) > 2 * 1024 * 1024) {
            throw new Error("价格 JSON 文件不能超过 2 MiB。");
          }
          const text = await file.text();
          const textarea = document.querySelector(`#${settingsModalId} [data-pricing-import-text="true"]`);
          if (textarea) textarea.value = text;
          setSettingsStatus(`已读取 ${file.name}，等待校验。`);
        } catch (error) {
          setSettingsStatus(`价格文件读取失败：${error?.message || error}`, "error");
        }
      }

      function previewPricingImport() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        const textarea = layer?.querySelector('[data-pricing-import-text="true"]');
        const effectiveInput = layer?.querySelector('[data-pricing-import-effective-at="true"]');
        try {
          const payload = JSON.parse(String(textarea?.value || ""));
          const defaultEffectiveAt = pricingEffectiveAtFromInput(effectiveInput);
          pricingWorkflowState.importSourcePayload = payload;
          pricingWorkflowState.importPayload = payload;
          pricingWorkflowState.importDefaultEffectiveAt = defaultEffectiveAt;
          submitSettingsCommand({
            action: "pricingImportPreview",
            payload,
            defaultEffectiveAt,
          }, "正在校验价格 JSON 并生成导入预览...");
        } catch (error) {
          setSettingsStatus(`价格 JSON 无法预览：${error?.message || error}`, "error");
        }
      }

      function openPricingImportPreview(preview, payload) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.pendingMode = "import";
        pricingWorkflowState.importPreview = preview;
        pricingWorkflowState.impactPreview = null;
        pricingWorkflowState.impactEffectiveAt = "";
        pricingWorkflowState.impactRequestId = "";
        pricingWorkflowState.importSourcePayload = pricingWorkflowState.importSourcePayload || payload || null;
        pricingWorkflowState.importPayload = payload || pricingWorkflowState.importPayload;
        const added = Number(preview?.addedCount ?? preview?.added ?? 0);
        const updated = Number(preview?.updatedCount ?? preview?.updated ?? 0);
        const skipped = Number(preview?.skippedCount ?? preview?.skipped ?? 0);
        const conflicts = Array.isArray(preview?.conflicts) ? preview.conflicts : [];
        const warnings = Array.isArray(preview?.warnings) ? preview.warnings : [];
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-pricing-dialog" role="alertdialog" aria-modal="true" aria-label="确认价格导入">
            <div class="codex-usage-hud-settings-confirm-kicker">导入预览</div>
            <div class="codex-usage-hud-settings-confirm-title">新增 ${added} · 更新 ${updated} · 跳过 ${skipped}</div>
            <div class="codex-usage-hud-pricing-preview-grid"><span>冲突<strong>${conflicts.length}</strong></span><span>兼容提示<strong>${warnings.length}</strong></span></div>
            ${conflicts.length ? `<div class="codex-usage-hud-pricing-preview-list">${conflicts.slice(0, 8).map((item) => `<div>${escapeHtml(String(item.provider || "全局"))} · ${escapeHtml(String(item.model_pattern || item.model || "模型"))} · ${escapeHtml(String(item.effective_at || ""))}</div>`).join("")}</div>` : ""}
            ${warnings.length ? `<div class="codex-usage-hud-pricing-impact">${warnings.map((item) => escapeHtml(String(item))).join("<br>")}</div>` : ""}
            <label class="codex-usage-hud-pricing-field">缺省的新价格生效时间
              <input type="datetime-local" data-pricing-import-effective-at="true" value="${escapeHtml(pricingLocalDateTimeValue(pricingWorkflowState.importDefaultEffectiveAt || new Date()))}" max="${escapeHtml(pricingLocalDateTimeValue())}">
            </label>
            <div class="codex-usage-hud-settings-confirm-impact" data-pricing-impact-preview="true">正在估算已知历史用量...</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-import-commit" data-primary="true" disabled>${conflicts.length ? "覆盖冲突并导入" : "确认导入"}</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
        const effectiveInput = layer.querySelector('[data-pricing-import-effective-at="true"]');
        if (effectiveInput) {
          let refreshTimer = 0;
          const refresh = () => {
            if (refreshTimer) clearTimeout(refreshTimer);
            refreshTimer = window.setTimeout(() => {
              refreshPricingImpactPreview({ mode: "import", input: effectiveInput });
            }, 120);
          };
          effectiveInput.addEventListener("input", refresh);
          effectiveInput.addEventListener("change", refresh);
          refreshPricingImpactPreview({ mode: "import", input: effectiveInput });
        }
      }

      function commitPricingImport() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        const input = layer?.querySelector('[data-pricing-import-effective-at="true"]');
        try {
          const effectiveAt = pricingEffectiveAtFromInput(input);
          if (
            !pricingWorkflowState.impactPreview
            || pricingWorkflowState.impactEffectiveAt !== effectiveAt
          ) {
            refreshPricingImpactPreview({ mode: "import", input });
            setSettingsStatus("正在刷新价格影响预览，请稍后确认。", "info");
            return;
          }
          pricingWorkflowState.importDefaultEffectiveAt = effectiveAt;
          submitSettingsCommand({
            action: "pricingImportCommit",
            payload: pricingImportPayloadForEffectiveAt(effectiveAt),
            defaultEffectiveAt: effectiveAt,
            conflictPolicy: "overwrite",
          }, "正在原子写入价格版本...");
        } catch (error) {
          setSettingsStatus(error?.message || String(error), "error");
        }
      }

      function copyPricingExample() {
        const example = {
          model: "your-model",
          provider: "your-provider",
          base_url: "https://api.example.com/v1",
          input: 1,
          cached_input: 0.1,
          cache_write: 1.25,
          output: 6,
          reasoning: 6,
          effective_at: new Date().toISOString(),
        };
        copyHudText(JSON.stringify(example, null, 2)).then(
          () => setSettingsStatus("已复制最小合法价格示例。"),
          (error) => setSettingsStatus(`复制示例失败：${error?.message || error}`, "error"),
        );
      }

      function openPricingRecalculationDialog() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const provider = String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card codex-usage-hud-pricing-dialog" role="dialog" aria-modal="true" aria-label="按价格版本重算历史费用">
            <div class="codex-usage-hud-settings-confirm-kicker">显式历史操作</div>
            <div class="codex-usage-hud-settings-confirm-title">按价格版本重算历史费用</div>
            <div class="codex-usage-hud-pricing-scope-grid">
              <label class="codex-usage-hud-pricing-field">Provider<input data-pricing-recalc-provider="true" value="${escapeHtml(provider)}"></label>
              <label class="codex-usage-hud-pricing-field">模型<input data-pricing-recalc-model="true" placeholder="全部模型"></label>
              <label class="codex-usage-hud-pricing-field">开始时间<input type="datetime-local" data-pricing-recalc-start="true"></label>
              <label class="codex-usage-hud-pricing-field">结束时间<input type="datetime-local" data-pricing-recalc-end="true" max="${escapeHtml(pricingLocalDateTimeValue())}"></label>
            </div>
            <div class="codex-usage-hud-pricing-impact">先生成差异预览。执行后保留原始 token、费用与价格快照审计。</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-recalc-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-recalc-preview" data-primary="true">预览差异</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function previewPricingRecalculation() {
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        try {
          const startAt = pricingEffectiveAtFromInput(layer?.querySelector('[data-pricing-recalc-start="true"]'), { allowEmpty: true });
          const endAt = pricingEffectiveAtFromInput(layer?.querySelector('[data-pricing-recalc-end="true"]'), { allowEmpty: true });
          const scope = {
            provider: String(layer?.querySelector('[data-pricing-recalc-provider="true"]')?.value || "").trim().toLowerCase(),
            model: String(layer?.querySelector('[data-pricing-recalc-model="true"]')?.value || "").trim(),
            startAt,
            endAt,
          };
          if (startAt && endAt && new Date(startAt).getTime() > new Date(endAt).getTime()) throw new Error("开始时间不能晚于结束时间。");
          pricingWorkflowState.recalculationScope = scope;
          submitSettingsCommand({ action: "pricingRecalculationPreview", ...scope }, "正在生成历史费用差异预览...");
        } catch (error) {
          setSettingsStatus(error?.message || String(error), "error");
        }
      }

      function openPricingRecalculationPreview(preview) {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        pricingWorkflowState.recalculationPreview = preview;
        const matched = Number(preview?.matchedCount || 0);
        const changed = Number(preview?.changedCount || 0);
        const before = Number(preview?.previousTotalUsd || 0);
        const after = Number(preview?.nextTotalUsd || 0);
        const unavailable = Number(preview?.unavailableCount || 0);
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="确认历史费用重算">
            <div class="codex-usage-hud-settings-confirm-kicker">差异预览</div>
            <div class="codex-usage-hud-settings-confirm-title">${matched} 条记录中 ${changed} 条费用会变化</div>
            <div class="codex-usage-hud-pricing-preview-grid"><span>重算前<strong>$${before.toFixed(6)}</strong></span><span>重算后<strong>$${after.toFixed(6)}</strong></span><span>不可计价<strong>${unavailable}</strong></span></div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-recalc-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="pricing-recalc-execute" data-primary="true" ${changed ? "" : "disabled"}>确认重算</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function executePricingRecalculation() {
        submitSettingsCommand({
          action: "pricingRecalculationExecute",
          ...(pricingWorkflowState.recalculationScope || {}),
        }, "正在按价格版本重算所选历史费用...");
      }

      function saveSettingsFromModal({ section = "" } = {}) {
        const settings = collectSettingsForm();
        if (!section && pricingChanged(settings)) {
          openPricingEffectiveDialog({ mode: "save", settings });
          return;
        }
        const submitted = submitSettingsCommand(
          { action: "save", settings, ...(section ? { section } : {}) },
          section === "restReminder" ? "正在保存提醒设置..." : "正在保存设置..."
        );
        if (submitted) {
          settingsDirtyProviders.clear();
          renderSettingsProviderTabs();
        }
      }

      function fetchPricesFromModal() {
        const settings = collectSettingsForm();
        const provider = String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase();
        const url = String(settings?.provider_settings?.[provider]?.pricing_url || settings.pricing_url || "").trim();
        openPricingEffectiveDialog({ mode: "fetch", provider, url });
      }

      function restartHudFromModal() {
        submitSettingsCommand(
          { action: "restart", reason: "settings" },
          "重启请求已提交，等待 HUD daemon 处理..."
        );
      }

      function installDesktopOverlayFromModal() {
        submitSettingsCommand(
          { action: "installDesktopOverlay" },
          "正在准备安装气泡组件..."
        );
      }

      function enableDesktopOverlayFromModal() {
        submitSettingsCommand(
          { action: "enableDesktopOverlay" },
          "正在重新检测气泡组件..."
        );
      }

      function exitHudFromModal() {
        openSettingsLoading({
          kicker: "正在退出",
          title: "正在停止 HUD",
          body: "HUD 正在退出当前界面，并停止后台守护进程（如果正在运行）。",
        });
        const submitted = submitSettingsCommand(
          { action: "exit", reason: "settings", expiresAt: Date.now() + 10000 },
          "退出请求已提交，正在停止 HUD...",
          { preserveOverlay: true }
        );
        if (submitted) {
          ctx.lifecycle.timeout("settings_restart", () => {
            try {
              if (typeof window.__codexUsageHudRemove === "function") {
                window.__codexUsageHudRemove();
                return;
              }
            } catch (_) {}
            document.getElementById(rootId)?.remove();
            document.getElementById(styleId)?.remove();
          }, 120);
        }
      }

      function checkUpdateFromModal() {
        openSettingsLoading({
          kicker: "正在检查",
          title: "正在检查更新",
          body: "HUD daemon 正在查询 GitHub Release。通常只需 1 到 3 秒。",
          mode: "check-update",
        });
        submitSettingsCommand(
          { action: "checkUpdate" },
          "检查更新请求已提交，等待 HUD daemon 查询 GitHub Release...",
          { preserveOverlay: true }
        );
      }

      function installUpdateFromModal() {
        openSettingsLoading({
          kicker: "正在准备",
          title: "正在检查并准备安装更新",
          body: "HUD daemon 会先检查 GitHub Release，再后台下载 Windows 安装包。",
          mode: "install-update",
        });
        submitSettingsCommand(
          { action: "installUpdate" },
          "安装更新请求已提交，等待 HUD daemon 下载并启动安装器...",
          { preserveOverlay: true }
        );
      }

      function openSettingsExitConfirm() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="确认退出 HUD">
            <div class="codex-usage-hud-settings-confirm-kicker">退出 HUD</div>
            <div class="codex-usage-hud-settings-confirm-title">完全退出并停止守护进程？</div>
            <div class="codex-usage-hud-settings-confirm-body">这会完全退出 HUD，并停止后台守护进程（如果当前正在运行）。\n\n是否继续？</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit-cancel" data-variant="ghost">取消</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit-confirm" data-primary="true">退出 HUD</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function runUpdateAction() {
        const state = currentUpdateState();
        let pending = "更新操作请求已提交，等待 HUD daemon 处理...";
        if (String(state?.phase || "") === "downloading") {
          pending = "暂停下载请求已提交...";
        } else if (String(state?.phase || "") === "paused") {
          pending = "继续下载请求已提交...";
        } else if (String(state?.phase || "") === "ready") {
          pending = "正在打开已下载的安装程序...";
        }
        submitSettingsCommand(
          { action: "updateAction" },
          pending
        );
      }

      function dismissWarningsToday() {
        const root = document.getElementById(rootId);
        if (root) {
          setText(root, "topWarnings", "");
          root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
            node.hidden = true;
          });
        }
        submitSettingsCommand(
          { action: "dismissWarningsToday" },
          "今天不再显示预算预警。"
        );
      }

      function exportSettingsFromModal() {
        const settings = collectSettingsForm();
        const data = JSON.stringify({ user: settings }, null, 2);
        const blob = new Blob([data], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "codex-usage-hud-settings.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        ctx.lifecycle.timeout("settings_export", () => URL.revokeObjectURL(url), 1000);
        setSettingsStatus("已导出 JSON");
      }

      function priceClipboardValues(text) {
        const values = String(text ?? "").trim().split(/\s+/).filter(Boolean);
        if (values.length !== 5 || !values[0]) return null;
        const prices = values.slice(1).map((line) => {
          const value = line.replace(/^\$\s*/, "").trim();
          return /^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) ? value : "";
        });
        if (prices.some((value) => !value)) return null;
        return {
          model: values[0],
          input: prices[0],
          cached_input: prices[1],
          cache_write: prices[2],
          output: prices[3],
        };
      }

      function fillPriceRowFromClipboard(row, text) {
        if (!row?.matches?.('[data-price-row="true"]')) return false;
        const values = priceClipboardValues(text);
        if (!values) return false;
        const fields = [
          "model",
          "input",
          "cached_input",
          "cache_write",
          "output",
        ].map((name) => row.querySelector(`[data-price-field="${name}"]`));
        if (fields.some((field) => !field)) return false;
        const names = ["model", "input", "cached_input", "cache_write", "output"];
        fields.forEach((field, index) => {
          field.value = values[names[index]];
        });
        return true;
      }

      function addModelPriceRow(initialModel = "") {
        const rows = document.querySelector(`#${settingsModalId} [data-price-rows="true"]`);
        if (!rows) return;
        const row = document.createElement("div");
        row.className = "codex-usage-hud-price-row";
        row.dataset.priceRow = "true";
        row.innerHTML = `
          <input data-price-field="model" value="${escapeHtml(initialModel)}" aria-label="模型">
          <input data-price-field="input" type="number" min="0" step="0.000001" value="0" aria-label="输入单价">
          <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="0" aria-label="缓存读取单价">
          <input data-price-field="cache_write" type="number" min="0" step="0.000001" value="0" aria-label="缓存写入单价">
          <input data-price-field="output" type="number" min="0" step="0.000001" value="0" aria-label="输出单价">
          <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="" aria-label="渠道">
          <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="" aria-label="Base URL">
        `;
        rows.appendChild(row);
        markSettingsProviderDirty();
        const target = initialModel ? row.querySelector('[data-price-field="input"]') : row.querySelector("input");
        target?.focus?.();
      }

      function openSettingsDiscardConfirm() {
        const dialog = settingsDialogRoot();
        if (!dialog) return;
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.innerHTML = `
          <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="放弃未保存的 Provider 修改">
            <div class="codex-usage-hud-settings-confirm-kicker">未保存修改</div>
            <div class="codex-usage-hud-settings-confirm-title">关闭设置并放弃修改？</div>
            <div class="codex-usage-hud-settings-confirm-body">${settingsDirtyProviders.size} 个 Provider 仍有未保存修改。</div>
            <div class="codex-usage-hud-settings-confirm-actions">
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-discard-cancel" data-variant="ghost">继续编辑</button>
              <button type="button" class="codex-usage-hud-settings-action" data-action="settings-discard-confirm" data-primary="true">放弃修改</button>
            </div>
          </div>
        `;
        dialog.appendChild(layer);
      }

      function closeSettingsModal({ force = false } = {}) {
        const modal = document.getElementById(settingsModalId);
        if (!modal) return;
        captureSettingsProviderForm();
        if (!force && settingsDirtyProviders.size) {
          openSettingsDiscardConfirm();
          return;
        }
        closeSettingsConfirm();
        if (settingsActiveTab === "backgroundUsage") {
          captureBackgroundUsageScrollPositions();
          clearBackgroundUsageRequestTimeout("query");
          clearBackgroundUsageRequestTimeout("detail");
        }
        modal.hidden = true;
        writeSettingsUiState(false, settingsActiveTab);
        ensureRestReminderCountdownTicker();
        settingsProviderDraft = null;
        settingsDirtyProviders.clear();
      }

      function applySettingsPayload(root, payload) {
        themeDomain.apply(root, payload || {});
        renderUpdateButtons(root, payload || {});
        applySettingsCommandStatus(payload || {});
        restReminderDomain.apply(root, payload || {});
        refreshComposerBadgeState(root);
      }

    function install() {
      return true;
    }

    function apply(root, payload) {
      return applySettingsPayload(root, payload || {});
    }

    function dispose() {
      return true;
    }

    return {
      install,
      apply,
      dispose,
      settingsChromeMarkup,
      loadPersistedSupportImages,
      persistSupportImages,
      defaultHudSettings,
      hudSettingsFromPayload,
      normalizePriceModel,
      priceModelPatternMatches,
      configuredPriceModels,
      observedPriceModels,
      unknownPriceModels,
      settingsBridgeUrl,
      settingsPathLabel,
      appVersion,
      currentUpdateState,
      workOverlaySelectableMax,
      desktopOverlayDependency,
      desktopOverlayDependencyHtml,
      syncDesktopOverlayDependency,
      updateStateFromPayload,
      updateActionGlyph,
      renderUpdateButtons,
      thresholdText,
      pricingImpactPreviewHtml,
      renderPricingImpactPreview,
      refreshPricingImpactPreview,
      priceRowsHtml,
      detectedPriceModelsHtml,
      settingsProviderNames,
      cloneSettingsPriceTable,
      providerDraftFromSettings,
      ensureSettingsProviderDraft,
      settingsProviderTabBadge,
      settingsProviderMeta,
      settingsProviderTabsHtml,
      settingsProviderEditorHtml,
      revealSettingsProviderTab,
      renderSettingsProviderTabs,
      captureSettingsProviderForm,
      priceClipboardValues,
      fillPriceRowFromClipboard,
      updateSettingsProviderDraftStatus,
      markSettingsProviderDirty,
      renderSettingsProviderEditor,
      switchSettingsProvider,
      typedSettingsRequestId,
      renderSettingsModal,
      restoreOpenSettingsModal,
      settingsPanelHtml,
      restReminderStatusTitle,
      supportPanelHtml,
      aboutPanelHtml,
      setSettingsStatus,
      setSettingsRestartVisible,
      setSettingsActionState,
      formatRestReminderClock,
      formatRestReminderInputTime,
      formatRestReminderRemaining,
      syncRestReminderCountdown,
      ensureRestReminderCountdownTicker,
      updateAboutActionButtons,
      showSettingsRestartPrompt,
      setSettingsLoadingText,
      syncSettingsUpdateLoading,
      applySettingsCommandStatus,
      submitSettingsCommand,
      settingsDialogRoot,
      closeSettingsConfirm,
      openSettingsLoading,
      collectSettingsForm,
      saveSettingsFromModal,
      fetchPricesFromModal,
      confirmPricingEffectiveAt,
      openPricingImportDialog,
      readPricingImportFile,
      previewPricingImport,
      commitPricingImport,
      copyPricingExample,
      openPricingRecalculationDialog,
      previewPricingRecalculation,
      executePricingRecalculation,
      restartHudFromModal,
      installDesktopOverlayFromModal,
      enableDesktopOverlayFromModal,
      exitHudFromModal,
      checkUpdateFromModal,
      installUpdateFromModal,
      openSettingsExitConfirm,
      runUpdateAction,
      dismissWarningsToday,
      exportSettingsFromModal,
      addModelPriceRow,
      openSettingsDiscardConfirm,
      closeSettingsModal,
      applySettingsPayload,
    };
  }

  const settingsShellDomain = ctx.domains.register(
    "settings_shell",
    createSettingsShellDomain(ctx, shared),
  );
  const {
    settingsChromeMarkup,
    loadPersistedSupportImages,
    persistSupportImages,
    defaultHudSettings,
    hudSettingsFromPayload,
    normalizePriceModel,
    priceModelPatternMatches,
    configuredPriceModels,
    observedPriceModels,
    unknownPriceModels,
    settingsBridgeUrl,
    settingsPathLabel,
    appVersion,
    currentUpdateState,
    workOverlaySelectableMax,
    desktopOverlayDependency,
    desktopOverlayDependencyHtml,
    syncDesktopOverlayDependency,
    updateStateFromPayload,
    updateActionGlyph,
    renderUpdateButtons,
    thresholdText,
    pricingImpactPreviewHtml,
    renderPricingImpactPreview,
    refreshPricingImpactPreview,
    priceRowsHtml,
    detectedPriceModelsHtml,
    settingsProviderNames,
    cloneSettingsPriceTable,
    providerDraftFromSettings,
    ensureSettingsProviderDraft,
    settingsProviderTabBadge,
    settingsProviderMeta,
    settingsProviderTabsHtml,
      settingsProviderEditorHtml,
      revealSettingsProviderTab,
      renderSettingsProviderTabs,
      captureSettingsProviderForm,
      priceClipboardValues,
      fillPriceRowFromClipboard,
      updateSettingsProviderDraftStatus,
      markSettingsProviderDirty,
    renderSettingsProviderEditor,
    switchSettingsProvider,
    typedSettingsRequestId,
    renderSettingsModal,
    restoreOpenSettingsModal,
    settingsPanelHtml,
    restReminderStatusTitle,
    supportPanelHtml,
    aboutPanelHtml,
    setSettingsStatus,
    setSettingsRestartVisible,
    setSettingsActionState,
    formatRestReminderClock,
    formatRestReminderInputTime,
    formatRestReminderRemaining,
    syncRestReminderCountdown,
    ensureRestReminderCountdownTicker,
    updateAboutActionButtons,
    showSettingsRestartPrompt,
    setSettingsLoadingText,
    syncSettingsUpdateLoading,
    applySettingsCommandStatus,
    submitSettingsCommand,
    settingsDialogRoot,
    closeSettingsConfirm,
    openSettingsLoading,
    collectSettingsForm,
    saveSettingsFromModal,
    fetchPricesFromModal,
    confirmPricingEffectiveAt,
    openPricingImportDialog,
    readPricingImportFile,
    previewPricingImport,
    commitPricingImport,
    copyPricingExample,
    openPricingRecalculationDialog,
    previewPricingRecalculation,
    executePricingRecalculation,
    restartHudFromModal,
    installDesktopOverlayFromModal,
    enableDesktopOverlayFromModal,
    exitHudFromModal,
    checkUpdateFromModal,
    installUpdateFromModal,
    openSettingsExitConfirm,
    runUpdateAction,
    dismissWarningsToday,
    exportSettingsFromModal,
    addModelPriceRow,
    openSettingsDiscardConfirm,
    closeSettingsModal,
    applySettingsPayload,
  } = settingsShellDomain;
"""

TEXT = _TEXT_PREFIX + SETTINGS_SUPPORT_PANELS + _TEXT_SUFFIX

__all__ = ["TEXT"]
