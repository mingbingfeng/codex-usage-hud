"""Renderer settings support/about panel asset fragment."""

TEXT = r"""      function supportPanelHtml(settings, path) {
        const url = String(settings.support_url || "https://github.com/mingbingfeng/codex-usage-hud");
        const images = Array.isArray(currentPayload()?.supportImages) ? currentPayload().supportImages : [];
        const enabled = !!settings.rest_reminder_enabled;
        const interval = Math.min(180, Math.max(1, Math.round(Number(settings.rest_reminder_interval_minutes) || 45)));
        const breakMinutes = Math.min(10, Math.max(1, Math.round(Number(settings.rest_reminder_break_minutes) || 2)));
        const postpone = Math.min(30, Math.max(5, Math.round(Number(settings.rest_reminder_postpone_minutes) || 10)));
        const workStart = String(settings.rest_reminder_work_start_time || "09:00");
        const workEnd = String(settings.rest_reminder_work_end_time || "18:00");
        const lunchEnabled = settings.rest_reminder_lunch_enabled !== false;
        const lunchStart = String(settings.rest_reminder_lunch_start_time || "12:00");
        const lunchEnd = String(settings.rest_reminder_lunch_end_time || "13:30");
        const reminder = currentPayload()?.restReminder && typeof currentPayload().restReminder === "object"
          ? currentPayload().restReminder : {};
        const startTime = formatRestReminderInputTime(Number(reminder.timerStartedAtMs));
        const todayRestedSeconds = Math.max(0, Number(reminder.todayRestedSeconds) || 0);
        const todayRestedCount = Math.max(
          0,
          Math.round(Number(reminder.todayRestedCount) || 0),
          Math.round(Number(reminder.completedTodayCount) || 0),
        );
        const restSummary = `今日已休息 ${formatRestReminderRemaining(todayRestedSeconds)} 共${todayRestedCount}次`;
        const qrItems = images.map((item) => `
          <div class="codex-usage-hud-support-qr">
            <div class="codex-usage-hud-support-qr-title">
              <span>${escapeHtml(item?.label || "赞赏码")}</span>
              <span>${escapeHtml(item?.hint || "扫码支持")}</span>
            </div>
            <img src="${escapeHtml(item?.src || "")}" alt="${escapeHtml(item?.label || "赞赏码")}">
          </div>
        `).join("");
        return `
          <div class="codex-usage-hud-support">
            <div class="codex-usage-hud-rest-reminder-card">
              <div class="codex-usage-hud-rest-reminder-top">
                <span class="codex-usage-hud-rest-reminder-title">休息提醒</span>
                <label class="codex-usage-hud-rest-reminder-toggle" title="开启或关闭休息提醒">
                  <input type="checkbox" data-setting-key="rest_reminder_enabled" ${enabled ? "checked" : ""}>
                  <span class="codex-usage-hud-rest-reminder-track" aria-hidden="true"></span>
                </label>
                <div class="codex-usage-hud-rest-reminder-status" data-state="${escapeHtml(reminder.state || (enabled ? "work" : "disabled"))}" aria-live="polite">
                  <span data-rest-reminder-status-title="true">${escapeHtml(restReminderStatusTitle(reminder.state, enabled))}</span>
                  <b data-rest-reminder-remaining="true">--:--:--</b>
                </div>
              </div>
              <div class="codex-usage-hud-rest-reminder-grid">
                <label class="codex-usage-hud-rest-reminder-field" title="专注时长（分钟）">
                  <span>时长</span>
                  <input data-setting-key="rest_reminder_interval_minutes" type="number" min="1" max="180" step="1" value="${escapeHtml(interval)}" aria-label="专注时长（分钟）">
                </label>
                <label class="codex-usage-hud-rest-reminder-field" title="每次休息时长（分钟）；到时自动开始下一轮">
                  <span>休息</span>
                  <input data-setting-key="rest_reminder_break_minutes" type="number" min="1" max="10" step="1" value="${escapeHtml(breakMinutes)}" aria-label="休息时长（分钟）">
                </label>
                <label class="codex-usage-hud-rest-reminder-field" title="稍后提醒（分钟）">
                  <span>延后</span>
                  <input data-setting-key="rest_reminder_postpone_minutes" type="number" min="5" max="30" step="1" value="${escapeHtml(postpone)}" aria-label="稍后提醒（分钟）">
                </label>
                <label class="codex-usage-hud-rest-reminder-field" title="本轮开始时间，可校正当前轮">
                  <span>本轮</span>
                  <input data-rest-reminder-start-time="true" type="time" value="${escapeHtml(startTime)}" aria-label="本轮开始时间">
                </label>
              </div>
              <div class="codex-usage-hud-rest-reminder-schedule">
                <div class="codex-usage-hud-rest-reminder-slot" title="工作时间；时段外不运行提醒">
                  <span>工作</span>
                  <span class="codex-usage-hud-rest-reminder-range">
                    <input data-setting-key="rest_reminder_work_start_time" type="time" value="${escapeHtml(workStart)}" aria-label="上班时间">
                    <span class="codex-usage-hud-rest-reminder-dash">–</span>
                    <input data-setting-key="rest_reminder_work_end_time" type="time" value="${escapeHtml(workEnd)}" aria-label="下班时间">
                  </span>
                  <span></span>
                </div>
                <div class="codex-usage-hud-rest-reminder-slot" title="午休暂停区间">
                  <span>午休</span>
                  <span class="codex-usage-hud-rest-reminder-range">
                    <input data-setting-key="rest_reminder_lunch_start_time" type="time" value="${escapeHtml(lunchStart)}" aria-label="午休开始">
                    <span class="codex-usage-hud-rest-reminder-dash">–</span>
                    <input data-setting-key="rest_reminder_lunch_end_time" type="time" value="${escapeHtml(lunchEnd)}" aria-label="午休结束">
                  </span>
                  <label class="codex-usage-hud-rest-reminder-check" title="启用午休暂停">
                    <input data-setting-key="rest_reminder_lunch_enabled" type="checkbox" ${lunchEnabled ? "checked" : ""} aria-label="启用午休暂停">
                  </label>
                </div>
              </div>
              <div class="codex-usage-hud-rest-reminder-foot">
                <div class="codex-usage-hud-rest-reminder-summary" data-rest-reminder-summary="true" title="${escapeHtml(restSummary)}">${escapeHtml(restSummary)}</div>
                <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-test-notification" title="发送系统通知并预览提醒">测试</button>
                <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-save" title="保存提醒设置">保存</button>
              </div>
            </div>
            <div class="codex-usage-hud-support-note">如果这个 HUD 帮你节省了排查 token 和费用的时间，可以扫码支持维护。</div>
            <div class="codex-usage-hud-support-qr-grid">
              ${qrItems || '<div class="codex-usage-hud-support-note">赞赏码资源未加载，请等待 HUD 刷新。</div>'}
            </div>
            <div class="codex-usage-hud-support-note">项目链接：<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a></div>
            <div class="codex-usage-hud-support-note">当前配置文件：${escapeHtml(path || "未提供")}</div>
          </div>
        `;
      }

      function aboutPanelHtml(path) {
        return `
          <div class="codex-usage-hud-support">
            <div class="codex-usage-hud-support-note">当前版本：<strong>v${escapeHtml(appVersion())}</strong></div>
            <div class="codex-usage-hud-support-note">更新源：GitHub Releases / mingbingfeng/codex-usage-hud</div>
            <div class="codex-usage-hud-support-note">Windows 安装包：codex-usage-hud-v*-windows-x64-setup.exe</div>
            <div class="codex-usage-hud-support-note">自动更新会下载最新版安装包并启动安装器；安装器会先关闭正在运行的 HUD，再替换本地文件。</div>
            <div class="codex-usage-hud-support-note">当前配置文件：${escapeHtml(path || "未提供")}</div>
          </div>
        `;
      }

"""

__all__ = ["TEXT"]
