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
    refreshClients: document.querySelector('#btn-refresh-clients'),
    clientRows: document.querySelector('#client-rows'),
    clientStats: document.querySelector('#client-stats'),
    toast: document.querySelector('#toast'),
    appVersion: document.querySelector('#app-version'),
    appRemote: document.querySelector('#app-remote'),
    appDelta: document.querySelector('#app-delta'),
    appBase: document.querySelector('#app-base'),
    statusVersion: document.querySelector('#status-version'),
    updateButton: document.querySelector('#btn-update'),
    updateOutput: document.querySelector('#update-output'),
    updateSummary: document.querySelector('#update-summary'),
    updateDetails: document.querySelector('#update-details'),
  };

  const storedToken = localStorage.getItem(STORAGE_KEY) || '';
  const state = {
    token: storedToken,
    config: null,
    clients: [],
    retryingToken: false,
    lastTypedToken: '',
    lastUpdate: null,
  };

  const queryToken = new URLSearchParams(window.location.search).get('token');
  if (queryToken) {
    state.token = queryToken.trim();
    if (state.token) {
      localStorage.setItem(STORAGE_KEY, state.token);
    }
  }

  const STATUS_META = {
    pending: { label: 'Pending', icon: '🟡' },
    approved: { label: 'Approved', icon: '🟢' },
    internet_blocked: { label: 'WAN blocked', icon: '🛑' },
    paused: { label: 'Paused', icon: '⏸' },
    blocked: { label: 'Blocked', icon: '🔴' },
    whitelist: { label: 'Whitelisted', icon: '⭐' },
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

  function setToken(value, { persist = true, notify = true, refresh = true } = {}) {
    const token = (value || '').trim();
    state.token = token;
    state.lastTypedToken = token;
    if (persist) {
      if (token) {
        localStorage.setItem(STORAGE_KEY, token);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    }
    updateTokenInput();
    if (notify) {
      showToast(token ? 'Token saved locally' : 'Token cleared');
    }
    if (refresh) {
      refreshAll(true);
    }
  }

  function persistTokenFromInput(options = {}) {
    if (!elements.tokenInput) return false;
    const typed = elements.tokenInput.value.trim();
    if (!typed) {
      if (!state.token) {
        return false;
      }
      setToken('', { ...options, notify: options.notify ?? false, refresh: options.refresh ?? false });
      return true;
    }
    if (!options.force && typed === state.token) {
      return false;
    }
    setToken(typed, options);
    return true;
  }

  function maybeAdoptTokenFromInput() {
    if (!elements.tokenInput) return false;
    const typed = elements.tokenInput.value.trim();
    if (!typed || typed === state.token || typed === state.lastTypedToken) {
      return false;
    }
    state.lastTypedToken = typed;
    setToken(typed, { persist: true, notify: false, refresh: false });
    return true;
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
    let response;
    try {
      response = await fetch(url, fetchOptions);
    } catch (error) {
      console.error('Network error during API request', action, error);
      throw error;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      const message = data.error || response.statusText || 'Request failed';
      const enhanced = new Error(message);
      enhanced.response = { status: response.status, body: data };
      console.error('API error', action, { status: response.status, data });
      if (response.status === 401) {
        const retried = handleUnauthorized(action, data);
        enhanced.retrying = retried;
      }
      throw enhanced;
    }
    return data;
  }

  function handleUnauthorized(action, data) {
    const hint = data && data.hint ? ` — ${data.hint}` : '';
    const tokenConfigured = data && Object.prototype.hasOwnProperty.call(data, 'token_configured')
      ? data.token_configured
      : null;
    if (elements.tokenInput) {
      elements.tokenInput.classList.add('input--highlight');
      elements.tokenInput.focus();
      setTimeout(() => elements.tokenInput.classList.remove('input--highlight'), 1500);
      if (tokenConfigured === false) {
        elements.tokenInput.placeholder = 'Leave blank when UI token disabled';
      } else if (tokenConfigured === true) {
        elements.tokenInput.placeholder = 'Enter the UI API token and save it locally';
      }
    }
    if (action === 'status' && maybeAdoptTokenFromInput()) {
      state.retryingToken = true;
      showToast('Trying token from the form…');
      window.setTimeout(() => refreshAll(true), 200);
      return true;
    }
    state.retryingToken = false;
    showToast(`Unauthorized${hint}`, 'error');
    return false;
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
    form.querySelector('#poll-timeout').value = config.poll_timeout ?? 25;
    form.querySelector('#plugins-dir').value = config.plugins_dir || '';
    form.querySelector('#log-file').value = config.log_file || '';
    form.querySelector('#ui-token').value = config.ui_api_token || '';
    form.querySelector('#ui-base').value = config.ui_base_url || '';
    form.querySelector('#version-endpoint').value = config.version_endpoint || '';
    const ttlField = form.querySelector('#version-cache-ttl');
    if (ttlField) {
      ttlField.value = config.version_cache_ttl ?? '';
    }
    const updateTimeout = form.querySelector('#update-timeout');
    if (updateTimeout) {
      updateTimeout.value = config.update_timeout ?? '';
    }
    const updateZip = form.querySelector('#update-zip-url');
    if (updateZip) {
      updateZip.value = config.update_zip_url || '';
    }
    const enhancedToggle = form.querySelector('#enhanced-notifications');
    if (enhancedToggle) {
      enhancedToggle.checked = Boolean(config.enhanced_notifications);
    }
    const scheduleField = form.querySelector('#notification-schedule');
    if (scheduleField) {
      if (Array.isArray(config.notification_schedule)) {
        scheduleField.value = config.notification_schedule.join(', ');
      } else if (typeof config.notification_schedule === 'string') {
        scheduleField.value = config.notification_schedule;
      } else {
        scheduleField.value = '';
      }
    }
    form.querySelector('#client-state').value = config.client_state_file || '';
    form.querySelector('#nft-table').value = config.nft_table || '';
    form.querySelector('#nft-chain').value = config.nft_chain || '';
    form.querySelector('#nft-block').value = config.nft_block_set || '';
    form.querySelector('#nft-allow').value = config.nft_allow_set || '';
    const internetField = form.querySelector('#nft-internet-block');
    if (internetField) {
      internetField.value = config.nft_internet_block_set || '';
    }
    const nftBinary = form.querySelector('#nft-binary');
    if (nftBinary) {
      nftBinary.value = config.nft_binary || '';
    }
    const wanField = form.querySelector('#wan-interfaces');
    if (wanField) {
      if (Array.isArray(config.wan_interfaces)) {
        wanField.value = config.wan_interfaces.join(', ');
      } else if (typeof config.wan_interfaces === 'string') {
        wanField.value = config.wan_interfaces;
      } else {
        wanField.value = '';
      }
    }
    const fwPath = form.querySelector('#firewall-include-path');
    if (fwPath) {
      fwPath.value = config.firewall_include_path || '';
    }
    const fwSection = form.querySelector('#firewall-include-section');
    if (fwSection) {
      fwSection.value = config.firewall_include_section || '';
    }
    const leasesPath = form.querySelector('#dhcp-leases-path');
    if (leasesPath) {
      leasesPath.value = config.dhcp_leases_path || '';
    }
    const ipNeigh = form.querySelector('#ip-neigh-command');
    if (ipNeigh) {
      if (Array.isArray(config.ip_neigh_command)) {
        ipNeigh.value = config.ip_neigh_command.join(' ');
      } else {
        ipNeigh.value = config.ip_neigh_command || '';
      }
    }
    form.querySelector('#client-whitelist').value = (config.client_whitelist || []).join(', ');
  }

  function applyBadgeState(element, label, state, title) {
    if (!element) return;
    element.textContent = label;
    element.title = title || '';
    element.classList.remove('hero__badge--ok', 'hero__badge--warn', 'hero__badge--error', 'hero__badge--idle');
    if (state) {
      element.classList.add(`hero__badge--${state}`);
    }
  }

  function updateVersionStatus(version = {}) {
    if (elements.appVersion) {
      const installed = version.app || 'dev';
      elements.appVersion.textContent = `Installed ${installed}`;
    }
    if (elements.appBase) {
      const base = version.base_dir;
      elements.appBase.textContent = base ? `Base ${base}` : '';
    }
    const remote = version.remote || '';
    if (elements.appRemote) {
      elements.appRemote.textContent = remote ? `Online ${remote}` : 'Online —';
      elements.appRemote.title = version.remote_source || '';
    }
    const statusInfo = (() => {
      if (version.remote_error) {
        return { label: '⚠️ Check failed', state: 'error', title: version.remote_error };
      }
      switch (version.status) {
        case 'up_to_date':
          return { label: '🟢 Up to date', state: 'ok' };
        case 'update_available':
          return { label: '🟡 Update available', state: 'warn' };
        case 'ahead':
          return { label: '🔵 Ahead of release', state: 'ok' };
        case 'unknown':
        default:
          return { label: '… Checking', state: 'idle' };
      }
    })();
    applyBadgeState(elements.appDelta, statusInfo.label, statusInfo.state, statusInfo.title);

    if (elements.statusVersion) {
      const installed = version.app || 'dev';
      let summary = `Installed ${installed}`;
      if (remote) {
        summary += ` • Remote ${remote}`;
      }
      summary += ` • ${statusInfo.label}`;
      const checked = version.remote_checked ? new Date(version.remote_checked) : null;
      if (checked && !Number.isNaN(checked.getTime())) {
        summary += `\nLast check ${checked.toLocaleString()}`;
      }
      elements.statusVersion.textContent = summary;
      elements.statusVersion.title = version.remote_source || '';
    }
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
    if (data.auth && elements.tokenInput) {
      if (data.auth.token_required) {
        elements.tokenInput.placeholder = 'Enter the UI API token and save it locally';
      } else {
        elements.tokenInput.placeholder = 'Leave blank when UI token disabled';
      }
    }
    if (data.version) {
      updateVersionStatus(data.version);
    }
    populateConfig(data.config);
    populatePlugins(data.plugins);
    if (data.clients) {
      renderClients(data.clients.clients || []);
      if (data.clients.counts) {
        renderClientStatsFromCounts(data.clients.counts, data.clients.clients || []);
      }
    }
    renderUpdateState();
  }

  async function refreshAll(silent = false) {
    try {
      const data = await apiRequest('status');
      state.retryingToken = false;
      state.lastTypedToken = '';
      renderStatus(data);
      if (!silent) {
        showToast('Status updated');
      }
    } catch (error) {
      console.error('Status refresh failed', error);
      if (error.retrying) {
        return;
      }
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
      poll_timeout: elements.configForm.querySelector('#poll-timeout').value,
      plugins_dir: elements.configForm.querySelector('#plugins-dir').value,
      log_file: elements.configForm.querySelector('#log-file').value,
      ui_api_token: elements.configForm.querySelector('#ui-token').value,
      ui_base_url: elements.configForm.querySelector('#ui-base').value,
      version_endpoint: elements.configForm.querySelector('#version-endpoint').value,
      version_cache_ttl: elements.configForm.querySelector('#version-cache-ttl').value,
      update_timeout: elements.configForm.querySelector('#update-timeout').value,
      update_zip_url: elements.configForm.querySelector('#update-zip-url').value,
      enhanced_notifications: elements.configForm.querySelector('#enhanced-notifications').checked,
      notification_schedule: elements.configForm.querySelector('#notification-schedule').value,
      client_state_file: elements.configForm.querySelector('#client-state').value,
      nft_table: elements.configForm.querySelector('#nft-table').value,
      nft_chain: elements.configForm.querySelector('#nft-chain').value,
      nft_block_set: elements.configForm.querySelector('#nft-block').value,
      nft_allow_set: elements.configForm.querySelector('#nft-allow').value,
      nft_internet_block_set: elements.configForm.querySelector('#nft-internet-block').value,
      nft_binary: elements.configForm.querySelector('#nft-binary').value,
      wan_interfaces: elements.configForm.querySelector('#wan-interfaces').value,
      firewall_include_path: elements.configForm.querySelector('#firewall-include-path').value,
      firewall_include_section: elements.configForm.querySelector('#firewall-include-section').value,
      dhcp_leases_path: elements.configForm.querySelector('#dhcp-leases-path').value,
      ip_neigh_command: elements.configForm.querySelector('#ip-neigh-command').value,
      client_whitelist: elements.configForm.querySelector('#client-whitelist').value,
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

  function renderClientStatsFromCounts(counts = {}, clients = []) {
    if (!elements.clientStats) return;
    const total = clients.length;
    if (!total) {
      elements.clientStats.textContent = 'No clients discovered yet.';
      return;
    }
    const parts = [`${total} total`];
    for (const key of Object.keys(STATUS_META)) {
      const value = counts[key] || 0;
      if (value) {
        parts.push(`${STATUS_META[key].label}: ${value}`);
      }
    }
    const unknown = counts.unknown || 0;
    if (unknown) {
      parts.push(`Unknown: ${unknown}`);
    }
    elements.clientStats.textContent = parts.join(' • ');
  }

  function renderClientStats(clients = []) {
    const counts = {};
    for (const client of clients) {
      const status = client.status || 'unknown';
      counts[status] = (counts[status] || 0) + 1;
    }
    renderClientStatsFromCounts(counts, clients);
  }

  function formatLastSeen(client) {
    if (client.online) {
      return 'Online now';
    }
    const ts = parseInt(client.last_seen, 10);
    if (!ts) {
      return 'Unknown';
    }
    const delta = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    if (delta < 60) return `${delta}s ago`;
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
  }

  function formatTimestamp(value) {
    const ts = parseInt(value, 10);
    if (!ts) return 'Unknown';
    const date = new Date(ts * 1000);
    if (Number.isNaN(date.getTime())) return 'Unknown';
    return date.toLocaleString();
  }

  function renderUpdateState() {
    if (!elements.updateSummary || !elements.updateOutput) return;
    const info = state.lastUpdate;
    if (!info) {
      elements.updateSummary.textContent = 'No update attempts yet.';
      elements.updateOutput.textContent = 'Run the installer update to see output here.';
      if (elements.updateDetails) {
        elements.updateDetails.open = false;
      }
      return;
    }
    elements.updateSummary.textContent = info.summary || 'Update status unavailable.';
    elements.updateOutput.textContent = info.log || '';
    if (elements.updateDetails) {
      elements.updateDetails.open = Boolean(info.open);
    }
  }

  function renderClients(clients = []) {
    state.clients = clients;
    if (!elements.clientRows) return;
    const tbody = elements.clientRows;
    tbody.innerHTML = '';
    if (!clients.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 10;
      cell.className = 'empty';
      cell.textContent = 'No clients discovered yet.';
      row.appendChild(cell);
      tbody.appendChild(row);
      renderClientStats([]);
      return;
    }
    for (const client of clients) {
      const row = document.createElement('tr');
      const identifier = client.id || client.mac;
      row.dataset.clientId = identifier;
      row.dataset.mac = client.mac;
      row.dataset.status = client.status || 'unknown';

      const statusCell = document.createElement('td');
      const statusInfo = STATUS_META[client.status] || { label: client.status || 'Unknown', icon: '•' };
      const badge = document.createElement('span');
      badge.className = `status-badge status-${client.status || 'unknown'}`;
      badge.textContent = `${statusInfo.icon} ${statusInfo.label}`;
      statusCell.appendChild(badge);

      const idCell = document.createElement('td');
      idCell.textContent = identifier || '—';

      const hostCell = document.createElement('td');
      hostCell.textContent = client.hostname || '—';

      const macCell = document.createElement('td');
      macCell.textContent = client.mac || '—';

      const ipCell = document.createElement('td');
      ipCell.textContent = client.ip || '—';

      const ifaceCell = document.createElement('td');
      ifaceCell.textContent = client.interface || '—';

      const onlineCell = document.createElement('td');
      onlineCell.textContent = client.online ? '🟢 Yes' : '⚪ No';
      if (!client.online) {
        onlineCell.title = `Last activity ${formatLastSeen(client)}`;
      }

      const seenCell = document.createElement('td');
      seenCell.textContent = formatTimestamp(client.first_seen);

      const lastCell = document.createElement('td');
      lastCell.textContent = formatLastSeen(client);

      const actionsCell = document.createElement('td');
      actionsCell.className = 'actions';
      const actions = determineClientActions(client.status);
      for (const action of actions) {
        const button = document.createElement('button');
        button.className = 'btn btn-outline';
        button.dataset.clientAction = action;
        button.dataset.identifier = identifier;
        button.textContent = actionLabel(action);
        actionsCell.appendChild(button);
      }

      row.append(
        statusCell,
        idCell,
        hostCell,
        macCell,
        ipCell,
        ifaceCell,
        onlineCell,
        seenCell,
        lastCell,
        actionsCell,
      );
      tbody.appendChild(row);
    }
    renderClientStats(clients);
  }

  function determineClientActions(status) {
    switch (status) {
      case 'approved':
        return ['pause', 'block_internet', 'block_network', 'whitelist', 'forget'];
      case 'paused':
        return ['resume', 'block_internet', 'block_network', 'forget'];
      case 'blocked':
        return ['approve', 'block_internet', 'whitelist', 'forget'];
      case 'internet_blocked':
        return ['approve', 'block_network', 'whitelist', 'forget'];
      case 'whitelist':
        return ['block_internet', 'block_network', 'forget'];
      default:
        return ['approve', 'block_internet', 'block_network', 'whitelist', 'pause', 'forget'];
    }
  }

  function actionLabel(action) {
    switch (action) {
      case 'approve':
        return '✅ Approve';
      case 'block':
        return '🚫 Block';
      case 'block_internet':
        return '🌐🚫 Block internet';
      case 'block_network':
        return '⛔ Block network';
      case 'pause':
        return '⏸ Pause';
      case 'resume':
        return '▶ Resume';
      case 'whitelist':
        return '⭐ Whitelist';
      case 'forget':
        return '🗑 Forget';
      default:
        return action;
    }
  }

  async function refreshClients(silent = false) {
    try {
      const data = await apiRequest('clients');
      renderClients(data.clients || []);
      if (!silent) {
        showToast('Client list updated');
      }
    } catch (error) {
      if (!silent) {
        showToast(error.message || String(error), 'error');
      }
    }
  }

  async function clientAction(action, identifier) {
    if (!identifier) return;
    try {
      await apiRequest('client_action', { method: 'POST', body: { action, target: identifier } });
      const messages = {
        approve: 'Client approved',
        block: 'Client blocked',
        block_internet: 'Client WAN access blocked',
        block_network: 'Client fully blocked',
        whitelist: 'Client whitelisted',
        pause: 'Client paused',
        resume: 'Client resumed',
        forget: 'Client removed',
      };
      showToast(messages[action] || `Client ${action} request sent`);
      await refreshClients(true);
    } catch (error) {
      showToast(error.message || String(error), 'error');
    }
  }

  async function performUpdate() {
    if (!elements.updateButton) return;
    if (elements.updateButton.dataset.running === '1') {
      return;
    }
    elements.updateButton.dataset.running = '1';
    const previousLabel = elements.updateButton.textContent;
    elements.updateButton.textContent = 'Updating…';
    elements.updateButton.disabled = true;
    showToast('Downloading latest release…');
    try {
      const data = await apiRequest('update', { method: 'POST', body: {} });
      const finished = new Date();
      const duration = data.duration ? ` in ${Math.round(Number(data.duration))}s` : '';
      const version = data.version ? `Installed ${data.version}` : 'Update completed';
      state.lastUpdate = {
        summary: `Last update ${finished.toLocaleString()} — ${version}${duration}`,
        log: data.log || 'Installer completed with no output.',
        open: true,
      };
      renderUpdateState();
      if (data.restart) {
        showToast(data.restart, 'info');
      } else {
        showToast('Update completed');
      }
      await refreshAll(true);
    } catch (error) {
      const message = error.message || 'Update failed';
      state.lastUpdate = {
        summary: `Last update failed at ${new Date().toLocaleString()} — ${message}`,
        log:
          (error.response && error.response.body && error.response.body.error) ||
          message ||
          'Update failed',
        open: true,
      };
      renderUpdateState();
      showToast(message, 'error');
    } finally {
      if (elements.updateButton) {
        elements.updateButton.disabled = false;
        elements.updateButton.textContent = previousLabel;
        delete elements.updateButton.dataset.running;
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
      persistTokenFromInput({ notify: true, refresh: true, force: true });
    });

    elements.clearToken?.addEventListener('click', () => {
      setToken('', { notify: true, refresh: true });
    });

    elements.tokenInput?.addEventListener('change', () => {
      persistTokenFromInput({ notify: false, refresh: false });
    });

    elements.tokenInput?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        persistTokenFromInput({ notify: true, refresh: true, force: true });
      }
    });

    elements.refresh?.addEventListener('click', () => refreshAll());
    elements.updateButton?.addEventListener('click', () => {
      if (!window.confirm('Download and install the latest release from GitHub now?')) {
        return;
      }
      performUpdate();
    });
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
    elements.refreshClients?.addEventListener('click', (event) => {
      event.preventDefault();
      refreshClients();
    });
    elements.clientRows?.addEventListener('click', (event) => {
      const target = event.target.closest('button[data-client-action]');
      if (!target) return;
      event.preventDefault();
      clientAction(target.dataset.clientAction, target.dataset.identifier || target.dataset.mac);
    });
  }

  updateTokenInput();
  renderUpdateState();
  bindEvents();
  refreshAll(true);
})();
