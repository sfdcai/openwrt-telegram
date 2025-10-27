(() => {
  const API_ENDPOINT = '/cgi-bin/telebot.py';
  const STORAGE_KEY = 'telebot-ui-token';

  const elements = {
    tokenInput: document.querySelector('#api-token'),
    saveToken: document.querySelector('#btn-save-token'),
    clearToken: document.querySelector('#btn-clear-token'),
    refresh: document.querySelector('#btn-refresh'),
    start: document.querySelector('#btn-start'),
    stop: document.querySelector('#btn-stop'),
    statusRunning: document.querySelector('#status-running'),
    statusUpdated: document.querySelector('#status-updated'),
    systemInfo: document.querySelector('#system-info'),
    configForm: document.querySelector('#config-form'),
    saveConfig: document.querySelector('#btn-save-config'),
    testMessage: document.querySelector('#btn-test'),
    messageChat: document.querySelector('#message-chat'),
    messageText: document.querySelector('#message-text'),
    sendMessage: document.querySelector('#btn-send-message'),
    pluginSelect: document.querySelector('#plugin-select'),
    pluginArgs: document.querySelector('#plugin-args'),
    runPlugin: document.querySelector('#btn-run-plugin'),
    pluginOutput: document.querySelector('#plugin-output'),
    logOutput: document.querySelector('#log-output'),
    refreshLogs: document.querySelector('#btn-refresh-logs'),
    toast: document.querySelector('#toast'),
  };

  const state = {
    token: localStorage.getItem(STORAGE_KEY) || '',
    config: null,
  };

  function showToast(message, kind = 'info') {
    if (!elements.toast) return;
    elements.toast.textContent = message;
    elements.toast.dataset.kind = kind;
    elements.toast.classList.add('toast--visible');
    clearTimeout(showToast.timeout);
    showToast.timeout = setTimeout(() => {
      elements.toast.classList.remove('toast--visible');
    }, 4000);
  }

  async function apiRequest(action, options = {}) {
    const method = options.method || 'GET';
    const headers = options.headers || {};
    if (state.token) {
      headers['X-Auth-Token'] = state.token;
    }
    let url = `${API_ENDPOINT}?action=${encodeURIComponent(action)}`;
    const fetchOptions = { method, headers };
    if (method === 'POST') {
      headers['Content-Type'] = 'application/json';
      fetchOptions.body = JSON.stringify(options.body || {});
    }
    const response = await fetch(url, fetchOptions);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      const message = data.error || response.statusText || 'Request failed';
      throw new Error(message);
    }
    return data;
  }

  function updateTokenInput() {
    if (elements.tokenInput) {
      elements.tokenInput.value = state.token;
    }
  }

  function populateConfig(config) {
    if (!config) return;
    state.config = config;
    const form = elements.configForm;
    if (!form) return;
    form.querySelector('#bot-token').value = config.bot_token_masked || '';
    form.querySelector('#chat-default').value = config.chat_id_default ?? '';
    form.querySelector('#allowed-ids').value = (config.allowed_user_ids || []).join(', ');
    form.querySelector('#admin-ids').value = (config.admin_user_ids || []).join(', ');
    form.querySelector('#poll-timeout').value = config.poll_timeout ?? 25;
    form.querySelector('#plugins-dir').value = config.plugins_dir || '';
    form.querySelector('#log-file').value = config.log_file || '';
    form.querySelector('#ui-token').value = config.ui_api_token || '';
    form.querySelector('#ui-base').value = config.ui_base_url || '';
  }

  function populatePlugins(plugins = []) {
    if (!elements.pluginSelect) return;
    elements.pluginSelect.innerHTML = '';
    if (!plugins.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'No plugins found';
      elements.pluginSelect.appendChild(option);
      return;
    }
    for (const plugin of plugins) {
      const option = document.createElement('option');
      option.value = plugin.command.replace(/^\//, '');
      option.textContent = plugin.description ? `${plugin.command} — ${plugin.description}` : plugin.command;
      elements.pluginSelect.appendChild(option);
    }
  }

  function renderStatus(data) {
    if (!data) return;
    if (elements.statusRunning) {
      elements.statusRunning.textContent = data.bot?.running ? `Running (pid ${data.bot.pids.join(', ')})` : 'Stopped';
    }
    if (elements.statusUpdated) {
      const ts = new Date();
      elements.statusUpdated.textContent = ts.toLocaleString();
    }
    if (elements.systemInfo) {
      elements.systemInfo.textContent = data.system?.info || 'No system information available.';
    }
    if (elements.logOutput && data.log_tail !== undefined) {
      elements.logOutput.textContent = data.log_tail || 'No log entries found.';
    }
    populateConfig(data.config);
    populatePlugins(data.plugins);
  }

  async function refreshAll(silent = false) {
    try {
      const data = await apiRequest('status');
      renderStatus(data);
      if (!silent) {
        showToast('Status updated');
      }
    } catch (error) {
      if (!silent) {
        showToast(error.message || String(error), 'error');
      }
    }
  }

  async function saveConfig() {
    if (!elements.configForm) return;
    const payload = {
      bot_token: elements.configForm.querySelector('#bot-token').value,
      chat_id_default: elements.configForm.querySelector('#chat-default').value,
      allowed_user_ids: elements.configForm.querySelector('#allowed-ids').value,
      admin_user_ids: elements.configForm.querySelector('#admin-ids').value,
      poll_timeout: elements.configForm.querySelector('#poll-timeout').value,
      plugins_dir: elements.configForm.querySelector('#plugins-dir').value,
      log_file: elements.configForm.querySelector('#log-file').value,
      ui_api_token: elements.configForm.querySelector('#ui-token').value,
      ui_base_url: elements.configForm.querySelector('#ui-base').value,
    };
    try {
      await apiRequest('save_config', { method: 'POST', body: payload });
      showToast('Configuration saved');
      if (payload.ui_api_token && payload.ui_api_token !== state.token) {
        state.token = payload.ui_api_token;
        localStorage.setItem(STORAGE_KEY, state.token);
        updateTokenInput();
      }
      await refreshAll(true);
    } catch (error) {
      showToast(error.message || String(error), 'error');
    }
  }

  async function sendTestMessage() {
    try {
      await apiRequest('send_test', { method: 'POST', body: {} });
      showToast('Test message sent');
    } catch (error) {
      showToast(error.message || String(error), 'error');
    }
  }

  async function sendCustomMessage() {
    const chatId = elements.messageChat.value;
    const message = elements.messageText.value.trim();
    if (!message) {
      showToast('Message text is required', 'error');
      return;
    }
    try {
      await apiRequest('send_message', { method: 'POST', body: { chat_id: chatId, message } });
      showToast('Message sent');
      elements.messageText.value = '';
    } catch (error) {
      showToast(error.message || String(error), 'error');
    }
  }

  async function runSelectedPlugin() {
    const plugin = elements.pluginSelect.value;
    if (!plugin) {
      showToast('Select a plugin first', 'error');
      return;
    }
    const args = elements.pluginArgs.value.trim();
    try {
      const data = await apiRequest('run_plugin', {
        method: 'POST',
        body: { plugin, args },
      });
      elements.pluginOutput.textContent = data.output || '(no output)';
      showToast('Plugin executed');
      await refreshLogs(true);
    } catch (error) {
      showToast(error.message || String(error), 'error');
    }
  }

  async function refreshLogs(silent = false) {
    try {
      const data = await apiRequest('logs');
      elements.logOutput.textContent = data.log_tail || '(no log entries)';
      if (!silent) {
        showToast('Logs refreshed');
      }
    } catch (error) {
      if (!silent) {
        showToast(error.message || String(error), 'error');
      }
    }
  }

  async function controlBot(command) {
    try {
      await apiRequest('control', { method: 'POST', body: { command } });
      showToast(`Service ${command}ed`);
      setTimeout(() => refreshAll(true), 800);
    } catch (error) {
      showToast(error.message || String(error), 'error');
    }
  }

  function bindEvents() {
    elements.saveToken?.addEventListener('click', () => {
      state.token = elements.tokenInput.value.trim();
      localStorage.setItem(STORAGE_KEY, state.token);
      showToast('Token saved locally');
      refreshAll(true);
    });

    elements.clearToken?.addEventListener('click', () => {
      localStorage.removeItem(STORAGE_KEY);
      state.token = '';
      updateTokenInput();
      showToast('Token cleared');
    });

    elements.refresh?.addEventListener('click', () => refreshAll());
    elements.start?.addEventListener('click', () => controlBot('start'));
    elements.stop?.addEventListener('click', () => controlBot('stop'));
    elements.saveConfig?.addEventListener('click', (event) => {
      event.preventDefault();
      saveConfig();
    });
    elements.testMessage?.addEventListener('click', (event) => {
      event.preventDefault();
      sendTestMessage();
    });
    elements.sendMessage?.addEventListener('click', (event) => {
      event.preventDefault();
      sendCustomMessage();
    });
    elements.runPlugin?.addEventListener('click', (event) => {
      event.preventDefault();
      runSelectedPlugin();
    });
    elements.refreshLogs?.addEventListener('click', (event) => {
      event.preventDefault();
      refreshLogs();
    });
  }

  updateTokenInput();
  bindEvents();
  if (state.token) {
    refreshAll(true);
    refreshLogs(true);
  }
})();
