/**
 * Логика веб-панели управления Continuous Broadcast Stream Server на Alpine.js.
 * Обрабатывает WebSocket для логов, периодический опрос телеметрии,
 * отправку команд плееру и динамическое управление плагинами оверлеев.
 */

document.addEventListener('alpine:init', () => {
    Alpine.data('streamApp', () => ({
        // Текущая вкладка: 'dashboard', 'plugins', 'settings', 'logs'
        activeTab: 'dashboard',

        // Телеметрия системы
        telemetry: {
            streamer: {
                status: 'IDLE',
                current_file: null,
                consecutive_errors: 0,
                max_consecutive_errors: 10,
                target_rtsp_url: 'rtsp://localhost:8554/live',
                hls_url: 'http://localhost:8888/live'
            },
            playlist: {
                total: 0,
                current_index: -1,
                current_file: null,
                items: []
            },
            plugins: []
        },

        // Подключенные клиенты вещания
        clientsData: {
            global_audio_enabled: true,
            total_connected: 0,
            clients: []
        },

        // Фильтрация и редактирование клиентов
        clientFilterOs: 'all', // 'all' | 'windows' | 'linux' | 'android'
        editingClientId: null,
        editingClientName: '',
        editingClientOs: '',
        editingClientIp: '',
        editingClientScheduleMode: 'global', // 'global' | '24/7' | 'interval'
        editingClientScheduleStart: '08:00',
        editingClientScheduleEnd: '20:00',
        editingClientScheduleDays: [1, 2, 3, 4, 5, 6, 7],

        // Управление расписанием вещания (24/7 и интервалы)
        schedule: {
            mode: '24/7', // '24/7' | 'interval'
            start_time: '08:00',
            end_time: '20:00',
            days_of_week: [1, 2, 3, 4, 5, 6, 7],
            action_off: 'standby',
            is_active_now: true,
            server_time: '--:--:--',
            server_date: '',
            server_weekday: 1
        },
        savingSchedule: false,

        // Добавление и детальное редактирование устройства вручную
        showAddDeviceModal: false,
        showEditClientModal: false,
        newDevice: {
            ip: '',
            name: '',
            os: 'Android 13',
            schedule_mode: 'global',
            schedule_start: '08:00',
            schedule_end: '20:00'
        },

        // Модальное окно переименования файла
        showRenameModal: false,
        renameTarget: {
            oldName: '',
            newName: ''
        },

        // Настройки сервера
        config: {
            media_dir: 'media',
            scan_interval: 10,
            rtsp_target_url: 'rtsp://localhost:8554/live',
            mediamtx_hls_url: 'http://localhost:8888/live',
            circuit_breaker: {
                max_consecutive_errors: 10,
                healthy_playback_threshold_sec: 15.0
            },
            plugins: {},
            custom_plugins_meta: {}
        },

        // Модальное окно добавления плагина
        showAddPluginModal: false,
        addPluginTab: 'visual', // 'visual' | 'python'
        newPlugin: {
            type: 'text_ticker', // 'text_ticker' | 'filter' | 'image'
            name: '',
            title: '',
            // Для бегущей строки
            text: 'Эфир телеканала • Прямая трансляция',
            textMode: 'scroll',
            textSpeed: 120,
            textPosition: 'bottom',
            textFontSize: 24,
            textColor: 'white',
            textBoxEnabled: true,
            textBoxColor: '0x00000099',
            // Для фильтра
            filterPreset: 'color_boost',
            filterExpr: 'eq=brightness=0.03:contrast=1.12:saturation=1.2',
            // Для картинки
            imagePosition: 'bottom_left',
            imageScaleWidth: 140,
            imageOpacity: 0.9,
            // Для Python
            pythonName: '',
            pythonCode: ''
        },

        // Логи и WebSocket
        logs: [],
        logFilter: 'all', // 'all' | 'errors'
        autoScrollLogs: true,
        ws: null,
        wsConnected: false,

        // Авторизация и текущий пользователь
        currentUser: {
            username: '',
            display_name: '',
            auth_type: '',
            is_admin: false
        },
        changePasswordData: {
            oldPassword: '',
            newPassword: '',
            message: '',
            status: '',
            loading: false
        },
        adTestStatus: {
            testing: false,
            success: null,
            message: ''
        },
        adPasswordInput: '',
        showAdPassword: false,
        savingAdConfig: false,

        // Всплывающие уведомления (Toasts)
        toasts: [],

        // Индикаторы загрузки
        actionLoading: false,
        savingConfig: false,
        uploadingLogo: false,
        installingPlugin: false,

        // Инициализация при загрузке страницы
        async init() {
            await this.fetchCurrentUser();
            this.fetchStatus();
            this.fetchConfig();
            this.fetchSchedule();
            this.connectWebSocket();
            this.loadPluginTemplates();

            // Периодический опрос телеметрии и расписания каждые 2 секунды
            setInterval(() => {
                this.fetchStatus();
                this.fetchSchedule();
            }, 2000);
        },

        // -------------------------------------------------------------
        // Авторизация и безопасность
        // -------------------------------------------------------------

        async fetchCurrentUser() {
            try {
                const res = await fetch('/api/auth/me');
                if (res.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                if (res.ok) {
                    this.currentUser = await res.json();
                }
            } catch (err) {
                console.debug('Не удалось получить пользователя:', err);
            }
        },

        async logout() {
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch (err) {
                // ignore
            } finally {
                window.location.href = '/login';
            }
        },

        async changePassword() {
            this.changePasswordData.loading = true;
            this.changePasswordData.message = '';
            try {
                const res = await fetch('/api/auth/change-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        old_password: this.changePasswordData.oldPassword,
                        new_password: this.changePasswordData.newPassword,
                    })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.changePasswordData.status = 'success';
                    this.changePasswordData.message = 'Пароль успешно изменен!';
                    this.changePasswordData.oldPassword = '';
                    this.changePasswordData.newPassword = '';
                    this.showToast('Пароль успешно изменен', 'success');
                } else {
                    this.changePasswordData.status = 'error';
                    this.changePasswordData.message = data.detail || 'Не удалось сменить пароль';
                }
            } catch (err) {
                this.changePasswordData.status = 'error';
                this.changePasswordData.message = 'Ошибка соединения при смене пароля';
            } finally {
                this.changePasswordData.loading = false;
            }
        },

        async testAdConnection() {
            this.adTestStatus.testing = true;
            this.adTestStatus.message = '';
            try {
                const domainCfg = (this.config && this.config.auth && this.config.auth.domain) ? this.config.auth.domain : {};
                const payload = {
                    server: domainCfg.server,
                    port: domainCfg.port ? parseInt(domainCfg.port) : undefined,
                    use_ssl: Boolean(domainCfg.use_ssl),
                    domain: domainCfg.domain,
                    base_dn: domainCfg.base_dn,
                    service_user: domainCfg.service_user,
                    service_password: this.adPasswordInput || undefined
                };
                const res = await fetch('/api/auth/test-ad', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.adTestStatus.success = true;
                    this.adTestStatus.message = data.message || 'Связь с Active Directory успешно подтверждена';
                    this.showToast('Подключение к Active Directory активно!', 'success');
                } else {
                    this.adTestStatus.success = false;
                    this.adTestStatus.message = data.message || data.detail || 'Ошибка соединения с домен-контроллером';
                    this.showToast('Ошибка связи с Active Directory', 'error');
                }
            } catch (err) {
                this.adTestStatus.success = false;
                this.adTestStatus.message = 'Сетевая ошибка при проверке Active Directory';
            } finally {
                this.adTestStatus.testing = false;
            }
        },

        async saveAdConfig() {
            this.savingAdConfig = true;
            try {
                if (!this.config.auth) this.config.auth = {};
                if (!this.config.auth.domain) this.config.auth.domain = {};
                if (this.adPasswordInput) {
                    this.config.auth.domain.service_password = this.adPasswordInput;
                }

                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.config)
                });
                const data = await response.json();
                if (response.ok) {
                    this.showToast('Параметры Active Directory успешно сохранены и зашифрованы!', 'success');
                    this.adPasswordInput = '';
                    await this.fetchConfig();
                } else {
                    this.showToast('Ошибка сохранения: ' + (data.detail || 'Некорректные параметры'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при сохранении настроек AD', 'error');
            } finally {
                this.savingAdConfig = false;
            }
        },

        // -------------------------------------------------------------
        // Запросы к REST API
        // -------------------------------------------------------------

        async fetchStatus() {
            try {
                const response = await fetch('/api/status');
                if (response.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                if (response.ok) {
                    const data = await response.json();
                    this.telemetry = data;
                    if (data.clients) {
                        this.clientsData = data.clients;
                    }
                }
            } catch (err) {
                console.debug('Не удалось обновить статус:', err);
            }
        },

        async fetchConfig() {
            try {
                const response = await fetch('/api/config');
                if (response.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                if (response.ok) {
                    this.config = await response.json();
                    if (!this.config.plugins) this.config.plugins = {};
                    if (!this.config.auth) this.config.auth = { local: {}, domain: {} };
                    if (!this.config.auth.domain) this.config.auth.domain = {};
                }
            } catch (err) {
                this.showToast('Ошибка загрузки конфигурации', 'error');
            }
        },

        async saveConfig() {
            this.savingConfig = true;
            try {
                if (this.adPasswordInput && this.config.auth && this.config.auth.domain) {
                    this.config.auth.domain.service_password = this.adPasswordInput;
                }
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.config)
                });
                const data = await response.json();
                if (response.ok) {
                    this.showToast('Настройки и плагины успешно сохранены!', 'success');
                    this.adPasswordInput = '';
                    await this.fetchConfig();
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка сохранения: ' + (data.detail || 'Некорректные параметры'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при сохранении настроек', 'error');
            } finally {
                this.savingConfig = false;
            }
        },

        async skipTrack() {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/player/skip', { method: 'POST' });
                if (res.ok) {
                    this.showToast('Текущий трек пропущен', 'info');
                    await this.fetchStatus();
                }
            } catch (err) {
                this.showToast('Ошибка при пропуске трека', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async previousTrack() {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/player/previous', { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    this.showToast('Переход к предыдущему треку', 'info');
                    await this.fetchStatus();
                } else {
                    this.showToast(data.detail || 'Не удалось перейти к предыдущему треку', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка при переходе к предыдущему треку', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async playTrack(filename) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/player/play-track', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename })
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(`Запущен трек: ${filename}`, 'success');
                    await this.fetchStatus();
                } else {
                    this.showToast(data.detail || 'Не удалось запустить трек', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка при запуске трека', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async moveTrack(fromIndex, toIndex) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/playlist/move', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ from_index: fromIndex, to_index: toIndex })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.playlist) {
                        this.telemetry.playlist = data.playlist;
                    }
                    this.showToast('Порядок воспроизведения изменен', 'info');
                    await this.fetchStatus();
                } else {
                    this.showToast(data.detail || 'Ошибка изменения порядка', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка при перемещении элемента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async deleteFile(filename) {
            if (!confirm(`Вы действительно хотите безвозвратно удалить файл "${filename}" с диска сервера?`)) {
                return;
            }

            this.actionLoading = true;
            try {
                const res = await fetch('/api/playlist/delete-file', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename })
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(`Файл "${filename}" успешно удален!`, 'success');
                    if (data.playlist) {
                        this.telemetry.playlist = data.playlist;
                    }
                    await this.fetchStatus();
                } else {
                    this.showToast(data.detail || 'Ошибка при удалении файла', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при удалении файла', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        openRenameModal(filename) {
            this.renameTarget.oldName = filename;
            this.renameTarget.newName = filename;
            this.showRenameModal = true;
        },

        async submitRename() {
            const oldName = this.renameTarget.oldName;
            const newName = (this.renameTarget.newName || '').trim();

            if (!newName || newName === oldName) {
                this.showRenameModal = false;
                return;
            }

            this.actionLoading = true;
            try {
                const res = await fetch('/api/playlist/rename-file', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        old_filename: oldName,
                        new_filename: newName
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(data.message, 'success');
                    if (data.playlist) {
                        this.telemetry.playlist = data.playlist;
                    }
                    this.showRenameModal = false;
                    await this.fetchStatus();
                } else {
                    this.showToast(data.detail || 'Ошибка при переименовании файла', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при переименовании файла', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        startEditClient(client) {
            this.editingClientId = client.client_id;
            this.editingClientName = client.custom_name || client.hostname;
            this.editingClientOs = client.os_info || '';
            this.editingClientIp = client.ip || '';
            this.editingClientScheduleMode = client.schedule_mode || 'global';
            this.editingClientScheduleStart = client.schedule_start || '08:00';
            this.editingClientScheduleEnd = client.schedule_end || '20:00';
            this.editingClientScheduleDays = client.schedule_days ? [...client.schedule_days] : [1, 2, 3, 4, 5, 6, 7];
        },

        cancelEditClient() {
            this.editingClientId = null;
            this.editingClientName = '';
            this.editingClientOs = '';
            this.editingClientIp = '';
            this.editingClientScheduleMode = 'global';
            this.editingClientScheduleStart = '08:00';
            this.editingClientScheduleEnd = '20:00';
            this.editingClientScheduleDays = [1, 2, 3, 4, 5, 6, 7];
        },

        async saveClientName(clientId) {
            if (!this.editingClientName || !this.editingClientName.trim()) {
                this.showToast('Имя не может быть пустым', 'error');
                return;
            }
            this.actionLoading = true;
            try {
                const payload = {
                    client_id: clientId,
                    custom_name: this.editingClientName.trim(),
                    schedule_mode: this.editingClientScheduleMode,
                    schedule_start: this.editingClientScheduleStart,
                    schedule_end: this.editingClientScheduleEnd,
                    schedule_days: this.editingClientScheduleDays,
                };
                if (this.editingClientOs && this.editingClientOs.trim()) {
                    payload.os_info = this.editingClientOs.trim();
                }
                if (this.editingClientIp && this.editingClientIp.trim()) {
                    payload.ip = this.editingClientIp.trim();
                }
                const res = await fetch('/api/clients/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.editingClientId = null;
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Не удалось обновить данные клиента', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при обновлении данных клиента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        // -------------------------------------------------------------
        // Управление расписанием вещания (Global & Per-client)
        // -------------------------------------------------------------

        async fetchSchedule() {
            try {
                const res = await fetch('/api/schedule');
                if (res.ok) {
                    const data = await res.json();
                    this.schedule = { ...this.schedule, ...data };
                }
            } catch (err) {
                console.debug('Не удалось получить расписание:', err);
            }
        },

        async saveSchedule() {
            this.savingSchedule = true;
            try {
                const res = await fetch('/api/schedule', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mode: this.schedule.mode,
                        start_time: this.schedule.start_time,
                        end_time: this.schedule.end_time,
                        days_of_week: this.schedule.days_of_week,
                        action_off: this.schedule.action_off || 'standby',
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    this.schedule = { ...this.schedule, ...data.schedule };
                    if (data.clients) {
                        this.clientsData = data.clients;
                    }
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Не удалось сохранить расписание', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при сохранении расписания', 'error');
            } finally {
                this.savingSchedule = false;
            }
        },

        toggleScheduleDay(dayNum) {
            if (!this.schedule.days_of_week) this.schedule.days_of_week = [1, 2, 3, 4, 5, 6, 7];
            const idx = this.schedule.days_of_week.indexOf(dayNum);
            if (idx > -1) {
                if (this.schedule.days_of_week.length > 1) {
                    this.schedule.days_of_week.splice(idx, 1);
                } else {
                    this.showToast('Должен быть выбран хотя бы один день недели', 'warning');
                }
            } else {
                this.schedule.days_of_week.push(dayNum);
                this.schedule.days_of_week.sort();
            }
        },

        toggleClientScheduleDay(dayNum) {
            if (!this.editingClientScheduleDays) this.editingClientScheduleDays = [1, 2, 3, 4, 5, 6, 7];
            const idx = this.editingClientScheduleDays.indexOf(dayNum);
            if (idx > -1) {
                if (this.editingClientScheduleDays.length > 1) {
                    this.editingClientScheduleDays.splice(idx, 1);
                } else {
                    this.showToast('Должен быть выбран хотя бы один день недели', 'warning');
                }
            } else {
                this.editingClientScheduleDays.push(dayNum);
                this.editingClientScheduleDays.sort();
            }
        },

        isClientDaySelected(dayNum) {
            return (this.editingClientScheduleDays || [1, 2, 3, 4, 5, 6, 7]).includes(dayNum);
        },

        openEditClientModal(client) {
            this.editingClientId = client.client_id;
            this.editingClientName = client.custom_name || client.hostname;
            this.editingClientOs = client.os_info || '';
            this.editingClientIp = client.ip || '';
            this.editingClientScheduleMode = client.schedule_mode || 'global';
            this.editingClientScheduleStart = client.schedule_start || '08:00';
            this.editingClientScheduleEnd = client.schedule_end || '20:00';
            this.editingClientScheduleDays = client.schedule_days ? [...client.schedule_days] : [1, 2, 3, 4, 5, 6, 7];
            this.showEditClientModal = true;
        },

        closeEditClientModal() {
            this.showEditClientModal = false;
        },

        async saveEditingClient() {
            if (!this.editingClientName || !this.editingClientName.trim()) {
                this.showToast('Имя не может быть пустым', 'error');
                return;
            }
            this.actionLoading = true;
            try {
                const payload = {
                    client_id: this.editingClientId,
                    custom_name: this.editingClientName.trim(),
                    os_info: (this.editingClientOs || '').trim(),
                    ip: (this.editingClientIp || '').trim(),
                    schedule_mode: this.editingClientScheduleMode,
                    schedule_start: this.editingClientScheduleStart,
                    schedule_end: this.editingClientScheduleEnd,
                    schedule_days: this.editingClientScheduleDays
                };
                const res = await fetch('/api/clients/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showEditClientModal = false;
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Не удалось обновить настройки клиента', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при сохранении настроек клиента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        filteredClients() {
            const list = this.clientsData.clients || [];
            if (this.clientFilterOs === 'all') {
                return list;
            }
            return list.filter(c => (c.os_family || '').toLowerCase() === this.clientFilterOs);
        },

        getClientCountByOs(os) {
            const list = this.clientsData.clients || [];
            if (os === 'all') return list.length;
            return list.filter(c => (c.os_family || '').toLowerCase() === os).length;
        },

        async toggleClientAudio(clientId, currentEnabled) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/audio-control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: clientId,
                        audio_enabled: !currentEnabled
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'info');
                } else {
                    this.showToast(data.detail || 'Не удалось изменить статус звука', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка при управлении звуком клиента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async toggleAllAudio(targetEnabled) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/audio-control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: 'all',
                        audio_enabled: targetEnabled
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Ошибка при переключении звука', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при переключении звука', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async toggleClientStream(clientId, currentAllowed) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/stream-control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: clientId,
                        stream_allowed: !currentAllowed
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'info');
                } else {
                    this.showToast(data.detail || 'Не удалось изменить статус вещания', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при управлении вещанием клиента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async toggleAllStream(targetAllowed) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/stream-control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: 'all',
                        stream_allowed: targetAllowed
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Ошибка при переключении вещания', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при переключении вещания', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async toggleClientStandby(clientId, currentStandby) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/standby', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: clientId,
                        standby: !currentStandby
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'info');
                } else {
                    this.showToast(data.detail || 'Не удалось изменить режим Standby', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при переключении Standby', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async toggleAllStandby(targetStandby) {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/standby', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: 'all',
                        standby: targetStandby
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Ошибка при переключении Standby', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при переключении Standby', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async poweroffClient(clientId, action) {
            const isPcPoweroff = action === 'poweroff';
            const actionPrompt = isPcPoweroff ? 'ВЫКЛЮЧИТЬ компьютер устройства' : 'закрыть клиентское приложение';
            if (!confirm(`Вы действительно хотите ${actionPrompt}?`)) {
                return;
            }
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/poweroff', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: clientId,
                        action: action
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'warning');
                } else {
                    this.showToast(data.detail || 'Ошибка отправки команды выключения', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при выключении клиента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async deleteClient(clientId, clientName) {
            if (!confirm(`Удалить устройство "${clientName}" из сохраненных?`)) {
                return;
            }
            this.actionLoading = true;
            try {
                const res = await fetch(`/api/clients/${encodeURIComponent(clientId)}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Ошибка при удалении клиента', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при удалении клиента', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        copyToClipboard(text) {
            if (!text) return;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(() => {
                    this.showToast(`IP ${text} скопирован в буфер`, 'info');
                }).catch(() => {
                    this.fallbackCopyText(text);
                });
            } else {
                this.fallbackCopyText(text);
            }
        },

        fallbackCopyText(text) {
            const el = document.createElement('textarea');
            el.value = text;
            document.body.appendChild(el);
            el.select();
            document.execCommand('copy');
            document.body.removeChild(el);
            this.showToast(`IP ${text} скопирован`, 'info');
        },

        async executeAdb(client, action) {
            const targetIp = client.ip;
            if (!targetIp) {
                this.showToast('У устройства не указан IP адрес', 'error');
                return;
            }

            if (action === 'shutdown') {
                if (!confirm(`Вы действительно хотите ВЫКЛЮЧИТЬ Android-устройство "${client.custom_name || targetIp}" по ADB (reboot -p)?`)) {
                    return;
                }
            } else if (action === 'reboot') {
                if (!confirm(`Перезагрузить Android-устройство "${client.custom_name || targetIp}" по ADB?`)) {
                    return;
                }
            }

            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/adb-action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: client.client_id,
                        ip: targetIp,
                        action: action
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    if (data.success) {
                        this.showToast(data.message, 'success');
                    } else {
                        this.showToast(data.message || 'Ошибка выполнения ADB команды', 'warning');
                    }
                } else {
                    this.showToast(data.detail || 'Не удалось выполнить ADB команду', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при отправке ADB команды', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async addManualDevice() {
            if (!this.newDevice.ip || !this.newDevice.ip.trim()) {
                this.showToast('Введите IP адрес устройства', 'error');
                return;
            }
            this.actionLoading = true;
            try {
                const res = await fetch('/api/clients/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ip: this.newDevice.ip.trim(),
                        custom_name: this.newDevice.name.trim() || `ТВ-${this.newDevice.ip.trim()}`,
                        os_info: this.newDevice.os.trim() || 'Android'
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (data.state) {
                        this.clientsData = data.state;
                    }
                    this.showAddDeviceModal = false;
                    this.newDevice = { ip: '', name: '', os: 'Android 13' };
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.detail || 'Ошибка добавления устройства', 'error');
                }
            } catch (err) {
                this.showToast('Ошибка сети при добавлении устройства', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async triggerScan() {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/player/scan', { method: 'POST' });
                if (res.ok) {
                    this.showToast('Принудительное сканирование запущено', 'info');
                    setTimeout(() => this.fetchStatus(), 1000);
                }
            } catch (err) {
                this.showToast('Ошибка при запуске сканирования', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async resetBreaker() {
            this.actionLoading = true;
            try {
                const res = await fetch('/api/player/reset-breaker', { method: 'POST' });
                if (res.ok) {
                    this.showToast('Предохранитель сброшен. Трансляция возобновлена!', 'success');
                    await this.fetchStatus();
                }
            } catch (err) {
                this.showToast('Ошибка сброса аварии', 'error');
            } finally {
                this.actionLoading = false;
            }
        },

        async uploadLogo(event) {
            const file = event.target.files[0];
            if (!file) return;

            this.uploadingLogo = true;
            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch('/api/plugins/logo/upload', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    if (!this.config.plugins.logo) this.config.plugins.logo = {};
                    this.config.plugins.logo.image_path = data.path;
                    this.showToast('Файл логотипа успешно загружен', 'success');
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка загрузки: ' + (data.detail || 'Не удалось сохранить файл'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при загрузке логотипа', 'error');
            } finally {
                this.uploadingLogo = false;
                event.target.value = '';
            }
        },

        // -------------------------------------------------------------
        // Управление динамическими плагинами
        // -------------------------------------------------------------

        async loadPluginTemplates() {
            try {
                const res = await fetch('/api/plugins/templates');
                if (res.ok) {
                    const data = await res.json();
                    if (!this.newPlugin.pythonCode) {
                        this.newPlugin.pythonCode = data.python_starter;
                    }
                }
            } catch (err) {
                console.debug('Ошибка загрузки шаблонов плагинов:', err);
            }
        },

        openAddPluginModal() {
            this.newPlugin.name = '';
            this.newPlugin.title = '';
            this.newPlugin.pythonName = '';
            this.loadPluginTemplates();
            this.showAddPluginModal = true;
        },

        onPresetChange() {
            const presets = {
                color_boost: 'eq=brightness=0.03:contrast=1.12:saturation=1.2',
                warm_vintage: 'curves=vintage',
                vignette: 'vignette=PI/4',
                soft_blur: 'boxblur=2:1'
            };
            if (presets[this.newPlugin.filterPreset]) {
                this.newPlugin.filterExpr = presets[this.newPlugin.filterPreset];
            }
        },

        async createVisualPlugin() {
            if (!this.newPlugin.name.trim()) {
                this.showToast('Укажите системный ID плагина (латиницей)', 'error');
                return;
            }

            this.installingPlugin = true;
            let cfg = { enabled: true };

            if (this.newPlugin.type === 'text_ticker') {
                cfg = {
                    enabled: true,
                    text: this.newPlugin.text,
                    mode: this.newPlugin.textMode,
                    speed: Number(this.newPlugin.textSpeed),
                    position: this.newPlugin.textPosition,
                    font_size: Number(this.newPlugin.textFontSize),
                    font_color: this.newPlugin.textColor,
                    box_enabled: this.newPlugin.textBoxEnabled,
                    box_color: this.newPlugin.textBoxColor,
                    margin_y: 20
                };
            } else if (this.newPlugin.type === 'filter') {
                cfg = {
                    enabled: true,
                    filter_expr: this.newPlugin.filterExpr,
                    preset: this.newPlugin.filterPreset
                };
            } else if (this.newPlugin.type === 'image') {
                cfg = {
                    enabled: false,
                    image_path: '',
                    position: this.newPlugin.imagePosition,
                    scale_width: Number(this.newPlugin.imageScaleWidth),
                    opacity: Number(this.newPlugin.imageOpacity),
                    margin_x: 20,
                    margin_y: 20
                };
            }

            try {
                const res = await fetch('/api/plugins/custom/create-visual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        plugin_type: this.newPlugin.type,
                        name: this.newPlugin.name.trim().toLowerCase(),
                        title: this.newPlugin.title.trim() || this.newPlugin.name.trim(),
                        config: cfg
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(`Плагин '${data.schema.title}' успешно добавлен!`, 'success');
                    this.showAddPluginModal = false;
                    await this.fetchConfig();
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка: ' + (data.detail || 'Не удалось создать плагин'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при создании плагина', 'error');
            } finally {
                this.installingPlugin = false;
            }
        },

        async installPythonPlugin() {
            if (!this.newPlugin.pythonName.trim()) {
                this.showToast('Укажите имя для Python-плагина', 'error');
                return;
            }
            if (!this.newPlugin.pythonCode.trim()) {
                this.showToast('Код плагина не может быть пустым', 'error');
                return;
            }

            this.installingPlugin = true;
            try {
                const res = await fetch('/api/plugins/custom/upload-python', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.newPlugin.pythonName.trim().toLowerCase(),
                        code: this.newPlugin.pythonCode
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(`Python-плагин '${this.newPlugin.pythonName}' установлен!`, 'success');
                    this.showAddPluginModal = false;
                    await this.fetchConfig();
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка валидации кода: ' + (data.detail || 'Некорректный плагин'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при установке плагина', 'error');
            } finally {
                this.installingPlugin = false;
            }
        },

        async uploadPythonFile(event) {
            const file = event.target.files[0];
            if (!file) return;

            this.installingPlugin = true;
            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch('/api/plugins/custom/upload-python', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(`Файл плагина '${file.name}' успешно загружен и установлен!`, 'success');
                    this.showAddPluginModal = false;
                    await this.fetchConfig();
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка в коде плагина: ' + (data.detail || 'Не удалось загрузить'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при загрузке файла плагина', 'error');
            } finally {
                this.installingPlugin = false;
                event.target.value = '';
            }
        },

        async deletePlugin(pluginName, pluginTitle) {
            if (!confirm(`Вы действительно хотите удалить плагин "${pluginTitle || pluginName}"?`)) {
                return;
            }

            try {
                const res = await fetch(`/api/plugins/custom/${pluginName}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (res.ok) {
                    this.showToast(`Плагин '${pluginTitle || pluginName}' удален`, 'info');
                    await this.fetchConfig();
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка: ' + (data.detail || 'Не удалось удалить'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при удалении плагина', 'error');
            }
        },

        async uploadCustomPluginImage(event, pluginName) {
            const file = event.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch(`/api/plugins/custom/${pluginName}/upload-image`, {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    if (!this.config.plugins[pluginName]) this.config.plugins[pluginName] = {};
                    this.config.plugins[pluginName].image_path = data.path;
                    this.showToast('Изображение баннера успешно загружено', 'success');
                    await this.fetchStatus();
                } else {
                    this.showToast('Ошибка загрузки: ' + (data.detail || 'Не удалось сохранить'), 'error');
                }
            } catch (err) {
                this.showToast('Сетевая ошибка при загрузке изображения', 'error');
            } finally {
                event.target.value = '';
            }
        },

        // -------------------------------------------------------------
        // WebSocket для живых логов
        // -------------------------------------------------------------

        connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/logs`;

            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.wsConnected = true;
            };

            this.ws.onmessage = (event) => {
                try {
                    const logEntry = JSON.parse(event.data);
                    this.logs.push(logEntry);
                    if (this.logs.length > 500) {
                        this.logs.shift();
                    }
                    if (this.autoScrollLogs) {
                        this.$nextTick(() => {
                            const el = document.getElementById('logTerminal');
                            if (el) el.scrollTop = el.scrollHeight;
                        });
                    }
                } catch (e) {
                    console.error('Ошибка парсинга лога:', e);
                }
            };

            this.ws.onclose = () => {
                this.wsConnected = false;
                // Авто-переподключение через 3 секунды
                setTimeout(() => this.connectWebSocket(), 3000);
            };

            this.ws.onerror = () => {
                this.wsConnected = false;
            };
        },

        clearLogs() {
            this.logs = [];
        },

        get filteredLogs() {
            if (this.logFilter === 'errors') {
                return this.logs.filter(l => ['ERROR', 'CRITICAL', 'WARNING'].includes(l.level));
            }
            return this.logs;
        },

        // -------------------------------------------------------------
        // Вспомогательные функции UI
        // -------------------------------------------------------------

        showToast(message, type = 'info') {
            const id = Date.now();
            this.toasts.push({ id, message, type });
            setTimeout(() => {
                this.toasts = this.toasts.filter(t => t.id !== id);
            }, 4000);
        },

        getStatusBadgeClass(status) {
            switch (status) {
                case 'LIVE':
                    return 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30';
                case 'CRITICAL_ERROR':
                    return 'bg-rose-500/10 text-rose-400 border border-rose-500/30 animate-pulse';
                default:
                    return 'bg-amber-500/10 text-amber-400 border border-amber-500/30';
            }
        },

        getLogLevelClass(level) {
            switch (level) {
                case 'CRITICAL':
                    return 'text-purple-400 font-bold bg-purple-950/40 px-1.5 py-0.5 rounded';
                case 'ERROR':
                    return 'text-rose-400 font-bold bg-rose-950/40 px-1.5 py-0.5 rounded';
                case 'WARNING':
                    return 'text-amber-400 bg-amber-950/40 px-1.5 py-0.5 rounded';
                default:
                    return 'text-cyan-400';
            }
        }
    }));
});
